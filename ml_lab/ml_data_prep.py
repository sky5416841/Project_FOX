"""
ml_data_prep.py — ML 考題產生器:把 PO3 掃針切入點轉成監督式學習資料集

流程:
  1. 跨多市場/時框抓大量歷史 K 線(分頁抓,突破單次 500/1000 上限)。
  2. 復用 po3_engine 的自適應偵測,找出歷史每一次「PO3 掃針」切入點。
  3. 提取特徵 X(只用『歷史真能算出』的 OHLCV 衍生量)。
  4. 標記答案 y:前向追蹤,先到 2R 停利=1(Win),先到 1R 停損=0(Loss)。
  5. 輸出 ml_features_dataset.csv。

★★ 重要的科學誠實聲明(務必讀) ★★
  原指令要求把 Delta / CVD 斜率 / OBI 失衡度也當特徵 —— 但這三個是『訂單流』,
  幣安公開 API **無法取得歷史逐筆成交與歷史訂單簿**(fetch_trades 只回最近約
  1000 筆、fetch_order_book 只回當下快照)。要把它們填進三個月前的掃針,只能用
  未來/假資料 → 那是未來函數自欺,本專案明令禁止(見 QUANT_RESEARCH.md)。
  因此本資料集**只含 OHLCV 衍生特徵**。要納入訂單流的正路是:讓 live 交易員
  從現在起逐筆記錄 Delta/OBI,數週後累積成可訓練樣本(見 README 註)。

  另:此資料集承襲全站結論 —— 這些訊號扣費後負期望已被回測釘死,本 ML 是
  「特徵工程 + 誠實建模」的技能/作品練習,預期模型樣本外不會顯著優於基準線。
"""
import os
import sys
import time
import ccxt
import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import po3_engine as eng

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT", "BNB/USDT", "XRP/USDT",
           "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT", "DOT/USDT", "TRX/USDT"]
TIMEFRAMES = ["5m", "15m"]
ATR_LEN = 14
BARS_PER = 3000          # 每個 市場×時框 抓多少根歷史(分頁累積)
TP_R, SL_R = 2.0, 1.0    # 標籤:先到 +2R=Win、先到 -1R=Loss
MAX_HOLD = 80            # 前向追蹤上限(根)
OUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_features_dataset.csv")


def atr(df, n=ATR_LEN):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def fetch_history(ex, symbol, tf, bars):
    """分頁往回抓 bars 根歷史 K 線。"""
    tf_ms = ex.parse_timeframe(tf) * 1000
    since = ex.milliseconds() - bars * tf_ms
    out, cursor = [], since
    while len(out) < bars:
        chunk = ex.fetch_ohlcv(symbol, tf, since=cursor, limit=1000)
        if not chunk:
            break
        out += chunk
        cursor = chunk[-1][0] + tf_ms
        if len(chunk) < 1000:
            break
        time.sleep(ex.rateLimit / 1000)
    df = pd.DataFrame(out, columns=["ts", "open", "high", "low", "close", "vol"])
    return df.drop_duplicates("ts").reset_index(drop=True)


def label_2r(df, e):
    """前向追蹤:回傳 (label, 是否可用)。Win=1(先到 2R)、Loss=0(先到 1R)。"""
    i0, i1, bh, bl = e["box"]
    k = e["k"]
    if k + 1 >= len(df):
        return None
    entry = float(df["open"].iat[k + 1])
    if e["side"] == "bear":
        sl = e["price_h"] * (1 + eng.SL_BUFFER)
        risk = sl - entry
        tp = entry - TP_R * risk
        direction = -1
    else:
        sl = e["price_l"] * (1 - eng.SL_BUFFER)
        risk = entry - sl
        tp = entry + TP_R * risk
        direction = +1
    if risk <= 0:
        return None
    end = min(k + 1 + MAX_HOLD, len(df))
    for j in range(k + 1, end):
        h, l = float(df["high"].iat[j]), float(df["low"].iat[j])
        if direction < 0:
            if h >= sl:  return 0
            if l <= tp:  return 1
        else:
            if l <= sl:  return 0
            if h >= tp:  return 1
    return None     # 逾時未分勝負 → 丟棄(不污染標籤)


def extract_features(df, e, atr_arr, volma_arr):
    """只用 OHLCV 衍生、歷史可得的特徵。"""
    i0, i1, bh, bl = e["box"]
    k = e["k"]
    o, h, l, c = (float(df["open"].iat[k]), float(df["high"].iat[k]),
                  float(df["low"].iat[k]), float(df["close"].iat[k]))
    atr = atr_arr[k]
    if not np.isfinite(atr) or atr <= 0:
        return None
    level = e["level"]
    pierce = (h - level) if e["side"] == "bear" else (level - l)
    volma = volma_arr[k]
    return {
        "side_bear":     1 if e["side"] == "bear" else 0,
        "box_range_pct": (bh - bl) / bl,                       # 框震幅%
        "box_len":       i1 - i0,                              # 盤整持續根數
        "bars_since_box": k - i1,                              # 掃針距框結束幾根
        "wick_atr":      e["wick"] / atr,                      # 影線/ATR
        "pierce_atr":    pierce / atr,                         # 刺破深度/ATR
        "body_atr":      abs(c - o) / atr,                     # 實體/ATR
        "atr_pct":       atr / c,                              # 波動率(ATR/價)
        "vol_surge":     (float(df["vol"].iat[k]) / volma) if volma > 0 else 1.0,
        "ret_5":         (c / float(df["close"].iat[k - 5]) - 1) if k >= 5 else 0.0,
        "ret_20":        (c / float(df["close"].iat[k - 20]) - 1) if k >= 20 else 0.0,
    }


def build():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    rows = []
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            try:
                df = fetch_history(ex, symbol, tf, BARS_PER)
            except Exception as ex_:
                print(f"  [WARN] {symbol} {tf} 抓取失敗 → {ex_}")
                continue
            if len(df) < eng.QUANTILE_LB + 50:
                continue
            df["atr"] = atr(df, ATR_LEN)
            atr_arr = df["atr"].values
            volma_arr = df["vol"].rolling(20).mean().values
            boxes = eng._adaptive_boxes(df)
            events = eng._find_manipulations(df, boxes)
            n_added = 0
            for e in events:
                y = label_2r(df, e)
                if y is None:
                    continue
                feat = extract_features(df, e, atr_arr, volma_arr)
                if feat is None:
                    continue
                feat.update({"label": y, "symbol": symbol, "tf": tf,
                             "ts": int(df["ts"].iat[e["k"]])})
                rows.append(feat)
                n_added += 1
            print(f"  {symbol:10} {tf:>3}  {len(df)}根 框{len(boxes)} → 樣本 +{n_added}")

    data = pd.DataFrame(rows)
    if len(data):
        data = data.sort_values("ts").reset_index(drop=True)
        data.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
        win = int(data["label"].sum())
        print(f"\n✓ 資料集 {len(data)} 筆 → {OUT_CSV}")
        print(f"  Win(到2R) {win} / Loss {len(data)-win}  基準勝率 {win/len(data):.1%}")
        print("  ⚠ 只含 OHLCV 衍生特徵(訂單流無法回溯,見檔頭聲明)")
    else:
        print("✗ 沒有產生任何樣本(掃針太稀有或歷史不足)")


if __name__ == "__main__":
    build()
