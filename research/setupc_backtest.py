# -*- coding: utf-8 -*-
"""預先登記的單一驗證:Setup C(趨勢回調)歷史回測
================================================================
假說(2026/9/30訂死,不事後修改):CNN判定4h=up且信心≥90%時,用15m EMA回踩進場、
結構停損、前高停利(完全複用 cv_predict.py 正式程式碼,非另建近似規則——
這就是使用者#10/#11/#12/13真實交易用的同一套機制),扣費後長期為正期望值。

門檻(預先訂死):這是單一假說、只測一次,不會看結果後再切分幣別/R:R/時段。
  t > 1.64 = 一般顯著;我們自己加嚴到 t > 2.0 才算「值得繼續看」。

方法:直接 import 正式的 cv_predict.py 內部函式(模型、render、_levels、_two_lights),
只是把 fetch_last() 的即時抓取換成「歷史某個時間點往回推WINDOW根」,其餘完全不變。
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys, tempfile, time
import numpy as np
import pandas as pd
import ccxt
import torch
import torch.nn.functional as F
from torchvision import datasets
from scipy import stats

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from cv_train import SmallCNN, TF as IMG_TF
from cv_dataset_gen import render, WINDOW
import cv_predict as cp

SYMBOLS = ["BTC/USDT", "ETH/USDT"]
MONTHS_BACK = 12         # 回測涵蓋的歷史長度(月)——上次4個月因API單次1500根上限實際只抓到~15天,這次用分頁修正
STRIDE_15M = 20          # 每隔幾根15m棒檢查一次(20根=5小時),降低運算量
CONF_MIN = 0.90          # 預先登記的信心門檻
T_THRESH = 2.0           # 預先登記的加嚴門檻
TAKER_FEE = 0.0005


def classify_df(df, tf_label):
    """複製 cv_predict.classify() 的全部邏輯,只是不即時抓資料,直接吃傳入的歷史df。"""
    tmp = os.path.join(tempfile.gettempdir(), "cv_bt_%s.png" % tf_label)
    render(df, tmp)
    x = IMG_TF(datasets.folder.default_loader(tmp)).unsqueeze(0)
    with torch.no_grad():
        model = cp.load_model(cp._model_path_for_tf(tf_label))
        prob = F.softmax(model(x), dim=1)[0]
    probs = {c: float(prob[i]) for i, c in enumerate(cp.CLASSES)}
    cls = max(probs, key=probs.get)
    lv = cp._levels(df, cls)
    return {"cls": cls, "conf": probs[cls], "levels": lv}


TF_MS = {"15m": 15 * 60 * 1000, "4h": 4 * 60 * 60 * 1000}
CACHE_DIR = os.path.dirname(os.path.abspath(__file__))


def fetch_full(ex, symbol, tf, months):
    """分頁抓完整歷史(單次fetch_ohlcv有1500根上限,必須分頁才能真的涵蓋months個月)。
    結果快取成CSV,重跑此腳本不用重新打API。"""
    cache = os.path.join(CACHE_DIR, "_cache_setupc_%s_%s.csv" % (symbol.split("/")[0], tf))
    if os.path.exists(cache):
        return pd.read_csv(cache)
    ms_per_bar = TF_MS[tf]
    total_bars = int(months * 30 * 24 * 60 * 60 * 1000 / ms_per_bar)
    since = ex.milliseconds() - total_bars * ms_per_bar
    rows = []
    while True:
        for attempt in range(4):
            try:
                batch = ex.fetch_ohlcv(symbol, tf, since=since, limit=1000)
                break
            except Exception as e:
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + ms_per_bar
        if len(batch) < 1000 or since > ex.milliseconds():
            break
        time.sleep(0.15)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol"])
    df = df.drop_duplicates("ts").reset_index(drop=True)
    df = df.iloc[:-1].reset_index(drop=True)   # 丟掉可能還沒收完的最後一根
    df.to_csv(cache, index=False)
    return df


def run_symbol(ex, symbol):
    df15 = fetch_full(ex, symbol, "15m", MONTHS_BACK)
    df4h = fetch_full(ex, symbol, "4h", MONTHS_BACK)
    trades = []
    checked = 0
    for i in range(WINDOW, len(df15) - WINDOW, STRIDE_15M):
        win15 = df15.iloc[i - WINDOW:i].reset_index(drop=True)
        ts_now = df15["ts"].iloc[i - 1]
        # 對齊同一時間點之前最近的4h窗
        idx4h = df4h[df4h["ts"] <= ts_now].index
        if len(idx4h) < WINDOW:
            continue
        j = idx4h[-1]
        win4h = df4h.iloc[max(0, j - WINDOW + 1):j + 1].reset_index(drop=True)
        if len(win4h) < WINDOW:
            continue
        try:
            r4 = classify_df(win4h, "4h")
            r15 = classify_df(win15, "15m")
        except Exception:
            continue
        checked += 1
        bias_long = (r4["cls"] == "up") and (r4["conf"] >= CONF_MIN)
        if not bias_long:
            continue
        L = r15["levels"]
        entry, sl, tp = L.get("entry"), L.get("sl"), L.get("tp")
        if entry is None or sl is None or sl >= entry:
            continue
        last_closed = win15.iloc[-1]
        green = last_closed["close"] >= last_closed["open"]
        touched = last_closed["low"] <= entry * 1.002
        above_sl = last_closed["close"] > sl
        if not (green and touched and above_sl):
            continue
        # 觸發:往後掃實際走勢,看先打到sl還是tp(無前瞻,只用i之後的資料)
        future = df15.iloc[i:i + 500]
        result = None
        for _, r in future.iterrows():
            if r["low"] <= sl:
                result = "loss"; break
            if r["high"] >= tp:
                result = "win"; break
        if result is None:
            continue
        stop_pct = (entry - sl) / entry
        rr = (tp - entry) / (entry - sl)
        fee_r = 2 * TAKER_FEE / stop_pct
        net_r = (rr if result == "win" else -1.0) - fee_r
        trades.append({"symbol": symbol, "ts": ts_now, "entry": entry, "sl": sl, "tp": tp,
                       "rr": rr, "result": result, "net_r": net_r})
    print("  %s: 檢查了%d個時間點,觸發%d筆" % (symbol, checked, len(trades)))
    return pd.DataFrame(trades)


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    all_tr = []
    t0 = time.time()
    for sym in SYMBOLS:
        print("跑 %s ..." % sym)
        all_tr.append(run_symbol(ex, sym))
    trades = pd.concat(all_tr, ignore_index=True)
    trades.to_csv("setupc_backtest_trades.csv", index=False)
    print("\n耗時 %.0f 秒" % (time.time() - t0))

    print("\n=== 預先登記驗證結果(單次,不再切分) ===")
    n = len(trades)
    if n < 10:
        print("❌ 樣本太少(n=%d),這次驗證因資料不足無法下結論。" % n)
        return
    win = (trades["result"] == "win").mean()
    net = trades["net_r"].mean()
    t, _ = stats.ttest_1samp(trades["net_r"], 0)
    print("n=%d  勝率%.1f%%  平均R:R=%.2f  扣費後淨值%+.3fR/筆  t=%+.2f" % (
        n, win * 100, trades["rr"].mean(), net, t))
    print("\n預先訂好的門檻: t > %.1f 才算「值得繼續看」" % T_THRESH)
    if net > 0 and t > T_THRESH:
        print("✅ 通過預先登記的門檻——這是目前為止唯一一個事先定義、未經事後切分就通過的結果。")
    elif net > 0:
        print("🟡 方向為正但沒達到加嚴門檻(t=%.2f < %.1f)——誠實記為「不夠格」,不會再切分去救。" % (t, T_THRESH))
    else:
        print("❌ 扣費後為負——Setup C 這套機制,history上沒有支撐使用者實戰的3勝2敗是靠真實edge。")


if __name__ == "__main__":
    main()
