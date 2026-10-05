# -*- coding: utf-8 -*-
"""大時框 PO3 訊號追蹤器(BTC/ETH × 30m/1h)。

背景:PO3 全樣本扣費後負,但『大時框稀釋手續費』是唯一還活著的線索——
停損越寬(30m/1h),固定手續費佔比越小,淨 EV 可能翻正(30m 樣本內 t=2.08 顯著)。
高 R:R(>=3)是另一個活口。兩者交集最有戲。

本工具:只掃 BTC/ETH 的 30m/1h,一出現掃針就算出 R:R + 手續費佔比,
標記是否符合『高勝算條件』,符合就寫旗標 + log 提醒你。**不下單、純警報**。

用法:
  python po3_bigtf_scan.py            # 掃一次
  python po3_bigtf_scan.py --loop     # 每 5 分鐘持續掃(可被看門狗接管)
"""
import warnings; warnings.filterwarnings("ignore")
import sys, os, time, datetime
import ccxt
import po3_engine as eng

SYMBOLS = ["BTC/USDT", "ETH/USDT"]      # 只做主流(使用者守則)
TFS     = ["30m", "1h"]                 # 只掃大時框(手續費被稀釋)
TAKER   = 0.0005                        # 來回手續費折算用(單邊,×2)
RR_MIN  = 3.0                           # 高 R:R 門檻(資料裡的活口)
LOG     = "_bigtf_scan.log"
FLAG    = "_bigtf_flag.txt"
INTERVAL = 300
MAX_RUNTIME = 6 * 3600  # 每跑滿6小時優雅退出→看門狗5分內重啟,釋放累積記憶體


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch(ex, sym, tf, limit=500, tries=3):
    for a in range(tries):
        try:
            raw = ex.fetch_ohlcv(sym, tf, limit=limit)
            import pandas as pd
            df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])
            return df.iloc[:-1].reset_index(drop=True)   # 丟未收完的最後一根
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(1.0)


def scan_once(ex):
    hits = []
    for sym in SYMBOLS:
        for tf in TFS:
            try:
                df = fetch(ex, sym, tf)
                sig = eng.get_live_signal(df)
            except Exception as e:
                log("ERR %s %s -> %s" % (sym, tf, e))
                continue
            if sig is None:
                continue
            entry, sl, tp = sig["entry_price"], sig["sl"], sig["tp"]
            sd = abs(entry - sl)
            if sd <= 0:
                continue
            rr = abs(tp - entry) / sd
            stop_pct = sd / entry
            fee_R = 2 * TAKER / stop_pct          # 來回手續費折算成 R
            # 高勝算條件:大時框(已限定) + R:R 夠高 + 手續費佔比低
            qualify = (rr >= RR_MIN) and (fee_R <= 0.20)
            tag = "⭐符合高勝算" if qualify else "一般(不足門檻)"
            msg = ("%s %s %s 掃針 | entry%.4f sl%.4f tp%.4f | R:R %.2f 停損%.2f%% 手續費%.3fR -> %s"
                   % (sym.split("/")[0], tf, sig["signal"], entry, sl, tp, rr, stop_pct * 100, fee_R, tag))
            log(msg)
            if qualify:
                hits.append(msg)
    if hits:
        with open(FLAG, "w", encoding="utf-8") as f:
            f.write("大時框高勝算掃針出現 %s\n" % datetime.datetime.now().strftime("%m-%d %H:%M"))
            for h in hits:
                f.write(h + "\n")
    else:
        # 沒訊號 → 清掉舊旗標,避免留著幾小時前已過期的訊號被誤當成現在還有效
        try:
            os.remove(FLAG)
        except FileNotFoundError:
            pass
    return hits


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    loop = "--loop" in sys.argv
    if loop:
        log("=== 大時框追蹤器啟動(每%ds掃 BTC/ETH × 30m/1h) ===" % INTERVAL)
        started = time.time()
        while True:
            try:
                hits = scan_once(ex)
                if not hits:
                    log("本輪無符合高勝算的大時框掃針")
            except Exception as e:
                log("ERR loop %s" % e)
            if time.time() - started > MAX_RUNTIME:
                log("已跑滿 %.0fh,優雅退出讓看門狗重啟(釋放記憶體)" % (MAX_RUNTIME / 3600))
                break
            time.sleep(INTERVAL)
    else:
        hits = scan_once(ex)
        if hits:
            print("\n⭐ 有符合高勝算條件的掃針!已寫入 %s" % FLAG)
        else:
            print("\n目前 BTC/ETH 的 30m/1h 沒有符合高勝算條件的掃針訊號。")


if __name__ == "__main__":
    main()
