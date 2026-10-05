# -*- coding: utf-8 -*-
"""背景監控（可在網頁關閉時運作）：
  每 60 秒：把觸發的限價單成交 + 結算已觸發停損/停利/爆倉（用檔案鎖與網頁互斥）。
  每 15 分：跑 CNN，記 log，出現機械式進場確認時寫 _entry_flag.txt。
不會自己開倉；限價單成交依你事先掛好的價位（照劇本執行，非代你決策）。"""
import warnings; warnings.filterwarnings("ignore")
import time, datetime, sys
import research.cv_predict as cp
import ccxt
import paper_manual as pm

LOG = "_cnn_watch.log"
FLAG = "_entry_flag.txt"
FILL_EVERY = 60          # 秒：檢查限價成交 / 結算
CNN_EVERY = 900          # 秒：CNN 判定
MAX_HOURS = 72
MAX_RUNTIME = 6 * 3600   # 每跑滿6小時優雅退出→看門狗5分內重啟,釋放累積記憶體(限價單狀態已存檔,不會漏)

def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def manage_paper(ex):
    """成交限價單 + 結算持倉。回傳 (filled, closed) 供 log。"""
    with pm._FileLock():
        s = pm.load_state()
        filled = pm.check_pending(s, ex, verbose=False)
        closed = pm.settle(s, ex, verbose=False)
        pm.save_state(s)
    for f in filled:
        log("FILL 限價單成交 -> #%s %s %s @%g" % (f["id"], f["symbol"], f["side"], f["entry"]))
    for c in closed:
        log("EXIT #%s %s %s @%g -> %+.2f (%.2fR)" % (
            c["id"], c["symbol"], c["reason"], c["exit"], c["net_pnl"], c["R"]))
    return filled, closed

def _ohlcv(ex, sym, tf, limit, tries=3):
    """幣安 klines 冷抓偶爾逾時 → 重試(消 ERR 雜訊)。"""
    for a in range(tries):
        try:
            return ex.fetch_ohlcv(sym, tf, limit=limit)
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(1.0)

def cnn_check(ex):
    r4 = cp.classify('BTC/USDT', '4h')
    r15 = cp.classify('BTC/USDT', '15m')
    L = r15.get('levels') or {}
    entry, sl, tp = L.get('entry'), L.get('sl'), L.get('tp')
    o = _ohlcv(ex, 'BTC/USDT', '15m', 3)
    c = o[-2]
    oo, ll, cc = c[1], c[3], c[4]
    green = cc >= oo
    touched = (entry is not None) and (ll <= entry * 1.002)
    above_sl = (sl is not None) and (cc > sl)
    bias_long = (r4['cls'] == 'up')
    trig = bias_long and green and touched and above_sl
    log("CNN 4h=%s(%.0f%%) 15m=%s(%.0f%%) closedK O%.0f C%.0f %s | entry%s sl%s -> %s" % (
        r4['cls'], r4['conf']*100, r15['cls'], r15['conf']*100, oo, cc,
        'GREEN' if green else 'RED', entry, sl, 'TRIGGER' if trig else 'wait'))
    if trig:
        with open(FLAG, "w", encoding="utf-8") as f:
            f.write("CONFIRM 15m green candle held support\n")
            f.write("closedK O%.0f C%.0f  entry=%s sl=%s tp=%s rr=%s\n" % (
                oo, cc, entry, sl, tp, L.get('rr')))
        # 機器可讀版本,給 live_order.py --from-flag 直接讀,不用手動轉抄數字
        import json
        with open("_entry_flag.json", "w", encoding="utf-8") as f:
            json.dump({"symbol": "BTC/USDT", "side": "long",
                      "entry": entry, "sl": sl, "tp": tp, "rr": L.get("rr")}, f)
    return trig

log("=== watcher start (限價成交每%ds / CNN每%ds) ===" % (FILL_EVERY, CNN_EVERY))
ex = ccxt.binance({'options': {'defaultType': 'future'}, 'enableRateLimit': True})
last_cnn = 0.0
started = time.time()
while True:
    try:
        manage_paper(ex)
        if time.time() - last_cnn >= CNN_EVERY:
            cnn_check(ex)
            last_cnn = time.time()
    except Exception as e:
        log("ERR %s" % e)
    if time.time() - started > MAX_RUNTIME:   # 防記憶體漏:定時優雅退出,看門狗5分內重啟(狀態已存檔不會漏單)
        log("已跑滿 %.0fh,優雅退出讓看門狗重啟(釋放記憶體)" % (MAX_RUNTIME / 3600))
        break
    time.sleep(FILL_EVERY)
