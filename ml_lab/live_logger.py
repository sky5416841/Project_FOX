"""
live_logger.py — 即時微觀特徵孵化器(ML 數據孵化計畫)

每當 live 偵測到 PO3 掃針結構,就把當下的特徵打包寫進 ml_lab/live_ml_features.csv:
  · OHLCV 衍生特徵 —— 復用 ml_data_prep.extract_features,保證欄位與離線訓練集一致
  · 機構級訂單流特徵 —— Delta、CVD 斜率、OBI 失衡度(由呼叫端即時捕獲後傳入)
  · 唯一識別碼 trade_id = 掃針那根 K 棒的 ts,供延遲打標回填

延遲打標(deferred labeling):掃針當下不知勝負,label 先留空;之後每輪
backfill() 用最新 K 線前向追蹤 2R/1R,把真實 1(Win)/0(Loss) 回填。逾時記 -1 丟棄。

★ 這就是把『訂單流無法歷史回溯』的死穴,改成『從現在起累積』的正路。
  數週後 live_ml_features.csv 就會是一份『含真實訂單流特徵』的可訓練資料集。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import po3_engine as eng
import ml_data_prep as mdp     # 復用 atr / extract_features,欄位才不會 drift

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "live_ml_features.csv")
TP_R, SL_R, MAX_HOLD = 2.0, 1.0, 80

FEATURE_COLS = ["side_bear", "box_range_pct", "box_len", "bars_since_box", "wick_atr",
                "pierce_atr", "body_atr", "atr_pct", "vol_surge", "ret_5", "ret_20"]
COLS = (["trade_id", "datetime", "symbol", "tf", "side", "entry", "sl", "tp"]
        + FEATURE_COLS + ["delta", "cvd_slope", "obi_ratio", "opened", "label", "resolved_at"])


def _live_event(df):
    """重建最新一根 K 棒的掃針 event(與 get_live_signal 同判定),供特徵抽取。"""
    k = len(df) - 1
    boxes = eng._adaptive_boxes(df)
    cand = [b for b in boxes if b[1] < k <= b[1] + eng.HUNT_BARS]
    if not cand:
        return None
    i0, i1, bh, bl = max(cand, key=lambda b: b[1])
    up_wick, dn_wick, up_thr, dn_thr = eng._wick_thresholds(df)
    if not (np.isfinite(up_thr[k]) and np.isfinite(dn_thr[k])):
        return None
    h, l, c = float(df["high"].iat[k]), float(df["low"].iat[k]), float(df["close"].iat[k])
    if h > bh * (1 + eng.PIERCE_MIN) and c < bh and up_wick[k] >= up_thr[k]:
        return {"box": (i0, i1, bh, bl), "k": k, "side": "bear", "level": bh,
                "wick": up_wick[k], "price_h": h, "price_l": l}
    if l < bl * (1 - eng.PIERCE_MIN) and c > bl and dn_wick[k] >= dn_thr[k]:
        return {"box": (i0, i1, bh, bl), "k": k, "side": "bull", "level": bl,
                "wick": dn_wick[k], "price_h": h, "price_l": l}
    return None


def ohlcv_features(df):
    """回傳最新掃針的 OHLCV 特徵 dict(欄位同訓練集),或 None。"""
    e = _live_event(df)
    if e is None:
        return None
    atr_arr = mdp.atr(df).values
    volma_arr = df["vol"].rolling(20).mean().values
    return mdp.extract_features(df, e, atr_arr, volma_arr)


def _load() -> pd.DataFrame:
    if os.path.exists(CSV):
        return pd.read_csv(CSV)
    return pd.DataFrame(columns=COLS)


def log_sweep(symbol, tf, df, signal, orderflow, opened) -> bool:
    """把一筆掃針特徵寫進孵化資料集(label 留空待回填)。回傳是否新增。"""
    feats = ohlcv_features(df)
    if feats is None:
        return False
    trade_id = int(df["ts"].iat[len(df) - 1])
    data = _load()
    if len(data) and (data["trade_id"] == trade_id).any():
        return False                              # 同根掃針已記錄,避免重複
    row = {
        "trade_id": trade_id,
        "datetime": pd.to_datetime(trade_id, unit="ms").strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol, "tf": tf, "side": signal["signal"],
        "entry": signal["entry_price"], "sl": signal["sl"], "tp": signal["tp"],
        **feats,
        "delta": round(orderflow.get("delta", float("nan")), 4),
        "cvd_slope": round(orderflow.get("cvd_slope", float("nan")), 6),
        "obi_ratio": round(orderflow.get("obi_ratio", float("nan")), 4),
        "opened": int(bool(opened)), "label": "", "resolved_at": "",
    }
    new = pd.DataFrame([row], columns=COLS)
    data = new if not len(data) else pd.concat([data, new], ignore_index=True)
    data.to_csv(CSV, index=False, encoding="utf-8-sig")
    return True


def backfill(symbol, tf, df) -> int:
    """對本市場/時框尚未打標的紀錄,用最新 K 線前向追蹤 2R/1R 回填 label。回傳回填筆數。"""
    if not os.path.exists(CSV):
        return 0
    data = _load()
    if not len(data):
        return 0
    data["label"] = data["label"].astype(object)          # 避免在 float 欄寫入字串的 dtype 警告
    data["resolved_at"] = data["resolved_at"].astype(object)
    # pending = label 尚未填(空字串讀回會變 NaN;已填的 0/1/-1 則為數值)
    unlabeled = pd.to_numeric(data["label"], errors="coerce").isna()
    pend = data[(data["symbol"] == symbol) & (data["tf"] == tf) & unlabeled]
    if not len(pend):
        return 0
    ts_arr = df["ts"].values
    n_done = 0
    for idx in pend.index:
        tid = int(data.at[idx, "trade_id"])
        if tid < ts_arr[0]:                       # 訊號比目前視窗還舊 → 無法解析,記逾時
            data.at[idx, "label"] = -1
            data.at[idx, "resolved_at"] = "timeout(out-of-window)"
            n_done += 1
            continue
        pos = np.searchsorted(ts_arr, tid)
        if pos >= len(ts_arr) or ts_arr[pos] != tid:
            continue
        entry, sl = float(data.at[idx, "entry"]), float(data.at[idx, "sl"])
        direction = -1 if data.at[idx, "side"] == "SHORT" else 1
        risk = abs(entry - sl)
        if risk <= 0:
            data.at[idx, "label"] = -1; data.at[idx, "resolved_at"] = "bad-risk"; n_done += 1; continue
        tp2 = entry - TP_R * risk if direction < 0 else entry + TP_R * risk
        end = min(pos + 1 + MAX_HOLD, len(df))
        label = None
        for j in range(pos + 1, end):
            h, l = float(df["high"].iat[j]), float(df["low"].iat[j])
            if direction < 0:
                if h >= sl: label = 0; break
                if l <= tp2: label = 1; break
            else:
                if l <= sl: label = 0; break
                if h >= tp2: label = 1; break
        if label is not None:
            data.at[idx, "label"] = label
            data.at[idx, "resolved_at"] = pd.to_datetime(int(ts_arr[min(j, len(ts_arr)-1)]), unit="ms").strftime("%Y-%m-%d %H:%M:%S")
            n_done += 1
        elif (len(df) - 1 - pos) >= MAX_HOLD:     # 追蹤夠久仍未分勝負 → 逾時丟棄
            data.at[idx, "label"] = -1
            data.at[idx, "resolved_at"] = "timeout(max-hold)"
            n_done += 1
    if n_done:
        data.to_csv(CSV, index=False, encoding="utf-8-sig")
    return n_done
