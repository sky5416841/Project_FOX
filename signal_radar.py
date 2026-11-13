# -*- coding: utf-8 -*-
"""signal_radar.py — 進場參謀雷達（只通知，不下單、不碰任何帳戶）。

每 15 分鐘看一次 BTC / ETH，條件符合就寫 radar_alerts.csv，並（若 .env 設了 DISCORD_WEBHOOK_URL）推播到 Discord：
  LONG_PULLBACK  4h=上漲(信心≥90%)，15m 紅轉綠回踩 EMA20（= Setup C 做多，與 demo_mirror 同規則）
  SHORT_BOUNCE   4h=下跌(信心≥90%)，15m 反彈碰 EMA20 後收紅（= H4 空頭鏡像規則）
  FLUSH          24h 跌幅 z ≤ −2.5（H6/H7 的爆倉洗盤訊號；H7 前瞻樣本，現貨、不加槓桿）
  FUNDING        資金費年化極端（≥ +30% 或 ≤ −15%）

★ 誠實聲明：這幾條規則的歷史回測扣費後都沒有優勢（Setup C −0.021R、H4 −0.049R、H6 −0.028R）。
  雷達是「幫你盯盤、算部位、守紀律」的工具，不是賺錢保證；每則通知都會附上這個歷史數字。
用法：python signal_radar.py            # 常駐（每 15 分鐘，滿 6 小時優雅退出給看門狗重啟）
      python signal_radar.py --once     # 只跑一輪並印出（不推播）
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys, json, csv, time, datetime, argparse, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE); sys.path.insert(0, HERE)
import numpy as np
from dotenv import load_dotenv
import risk_sizer as rs

LOG, ALERTS, STATE = "radar.log", "radar_alerts.csv", "radar_state.json"
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
CONF_MIN, EVERY, MAX_RUNTIME = 0.90, 900, 6 * 3600
FLUSH_Z, FUND_HI, FUND_LO = -2.5, 30.0, -15.0
COOLDOWN_H = {"LONG_PULLBACK": 4, "SHORT_BOUNCE": 4, "FLUSH": 24, "FUNDING": 24}
HIST = {"LONG_PULLBACK": "歷史回測 Setup C 做多扣費後 −0.021R/筆（無優勢）",
        "SHORT_BOUNCE": "歷史回測 H4 空頭鏡像扣費後 −0.049R/筆（無優勢）",
        "FLUSH": "H6 含停損 −0.028R/筆（無優勢）；H7『不設停損、只限現貨』前瞻累積中",
        "FUNDING": "資金費套利是結構性收益，但近一年年化僅約 3–5%"}


def rest(url):
    req = urllib.request.Request(url, headers={"User-Agent": "FOX-radar/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def paper_equity():
    try:
        return float(json.load(open("paper_manual_state.json", encoding="utf-8"))["equity"])
    except Exception:
        return 600.0


def fmt_price(x):
    return "{:,.2f}".format(x) if abs(x) >= 100 else ("{:,.4f}".format(x) if abs(x) >= 1 else "{:.6f}".format(x))


def sizing_text(side, entry, sl, tp):
    eq = paper_equity()
    r0 = rs.compute(eq, 1.0, side, entry, sl, tp, 5.0)
    if r0 is None:
        return ""
    lev = max(1.0, min(5.0, float(int(r0["max_safe_lev"]))))
    r = rs.compute(eq, 1.0, side, entry, sl, tp, lev)
    ok = r["sl_ok"] and r["tp_ok"] and r["stop_first"]
    return ("以權益 $%.0f、單筆風險 1%%（虧 $%.2f）：數量 %.4f、名目 $%.0f、建議槓桿 %gx、爆倉價 %s（%s）"
            % (eq, r["risk_amt"], r["qty"], r["notional"], lev, fmt_price(r["liq"]),
               "停損先於爆倉 ✅" if ok else "⚠ 停損不先於爆倉，別進"))


def send(kind, sym, text, row, state, dry, push=True):
    key = "%s|%s" % (kind, sym)
    last = state.get(key)
    if last and (time.time() - last) < COOLDOWN_H[kind] * 3600 and not dry:
        return False
    log("ALERT %s %s" % (kind, sym))
    if dry:
        print("\n" + text + "\n"); return True
    new = not os.path.exists(ALERTS)
    with open(ALERTS, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "symbol", "kind", "side", "entry", "sl", "tp", "rr", "note"])
        w.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sym, kind] + row)
    state[key] = time.time()
    json.dump(state, open(STATE, "w", encoding="utf-8"))
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip() if push else ""
    tw = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%m/%d %H:%M")
    text = "🕒 %s（台灣時間）\n%s" % (tw, text)
    if url:
        try:
            req = urllib.request.Request(url, data=json.dumps({"content": text[:1900]}).encode("utf-8"),
                                         headers={"Content-Type": "application/json", "User-Agent": "FOX-radar/1.0"})
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:
            log("DISCORD_FAIL %s" % type(e).__name__)
    return True


def setup_alert(kind, sym, side, lv, why):
    e, s, t, rr = lv["entry"], lv["sl"], lv["tp"], lv["rr"]
    icon = "🟢 做多" if side == "long" else "🔴 做空"
    text = ("**%s｜%s %s**\n%s\n進場 %s｜停損 %s｜停利 %s｜R:R %.2f\n%s\n"
            "⚠ %s\n（雷達只通知；要下 Demo 單請開 trade_desk 網頁，確認後再送。）"
            % (kind, sym, icon, why, fmt_price(e), fmt_price(s), fmt_price(t), rr,
               sizing_text(side, e, s, t), HIST[kind]))
    return text, [side, round(e, 6), round(s, 6), round(t, 6), round(rr, 2), why]


def _can_push():
    """H9：模擬倉啟用時，只有『真的能開單』的訊號才推 Discord；被略過的只寫紀錄，避免洗版。"""
    if os.environ.get("RADAR_AUTOTRADE") != "1":
        return True
    try:
        import radar_autotrade as at
        return at.can_open()
    except Exception:
        return True


def auto_open(kind, sym, side, lv, sig_open_ms, sig_close=None):
    """H8 自建模擬倉（純模擬）。只有電腦端 .env 設 RADAR_AUTOTRADE=1 才啟用。"""
    if os.environ.get("RADAR_AUTOTRADE") != "1":
        return
    try:
        import radar_autotrade as at
        at.on_signal(kind, sym, side, lv, sig_open_ms, sig_close)
    except Exception as e:
        log("AUTOTRADE_ERR %s" % str(e)[:160])


def check_symbol(sym, ex_f, state, dry):
    import research.cv_predict as cp
    r4 = cp.classify(sym, "4h")
    df15 = cp.fetch_last(sym, "15m", cp.WINDOW).reset_index(drop=True)
    o = ex_f.fetch_ohlcv(sym, "15m", limit=3)
    c = o[-2]; op, hi, lo, cl = c[1], c[2], c[3], c[4]
    log("%s CNN 4h=%s(%.0f%%) 已收15m O%.4g C%.4g" % (sym, r4["cls"], r4["conf"] * 100, op, cl))
    if r4["conf"] < CONF_MIN:
        return
    if r4["cls"] == "up":
        lv = cp._levels(df15, "up")
        if cl >= op and lo <= lv["entry"] * 1.002 and cl > lv["sl"] and lv["rr"] >= 1.5:
            text, row = setup_alert("LONG_PULLBACK", sym, "long", lv,
                                    "4h 上漲(%.0f%%)，15m 綠棒收在 EMA20 附近、守住支撐" % (r4["conf"] * 100))
            if send("LONG_PULLBACK", sym, text, row, state, dry, push=_can_push()) and not dry:
                auto_open("LONG_PULLBACK", sym, "long", lv, c[0], cl)
    elif r4["cls"] == "down":
        lv = cp._levels(df15, "down")
        if cl < op and hi >= lv["entry"] * 0.998 and cl < lv["sl"] and lv["sl"] > lv["entry"] > lv["tp"] and lv["rr"] >= 1.5:
            text, row = setup_alert("SHORT_BOUNCE", sym, "short", lv,
                                    "4h 下跌(%.0f%%)，15m 反彈碰到 EMA20 後收紅" % (r4["conf"] * 100))
            if send("SHORT_BOUNCE", sym, text, row, state, dry, push=_can_push()) and not dry:
                auto_open("SHORT_BOUNCE", sym, "short", lv, c[0], cl)


def check_flush(sym, ex_s, state, dry):
    k = rest("https://api.binance.com/api/v3/klines?symbol=%s&interval=4h&limit=700" % sym.replace("/", ""))[:-1]
    cl = np.array([float(x[4]) for x in k], float)
    if len(cl) < 560:
        return
    ret = np.r_[[np.nan] * 6, cl[6:] / cl[:-6] - 1]
    sig = np.nanstd(ret[-541:-1])             # 過去 540 根（不含當根），與 H6/H7 一致
    z = ret[-1] / sig if sig > 0 else 0
    log("%s 24h跌幅 z=%.2f" % (sym, z))
    if z <= FLUSH_Z:
        text = ("**FLUSH｜%s 爆倉洗盤訊號 🧯**\n24 小時變動 %+.1f%%，跌幅約為平常波動的 %.1f 倍（標準差）。\n"
                "歷史上這類洗盤後 24 小時內，約 74%% 會再破這輪最低點（接刀是常態）。\n"
                "H7（前瞻樣本）：下一根 4h 開盤買現貨、持有 24 小時、不設停損；只限現貨、不得加槓桿。\n⚠ %s"
                % (sym, ret[-1] * 100, abs(z), HIST["FLUSH"]))
        send("FLUSH", sym, text, ["info", round(cl[-1], 6), "", "", "", "z=%.2f" % z], state, dry)


def check_funding(sym, ex_f, state, dry):
    r = float(rest("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=%s" % sym.replace("/", ""))["lastFundingRate"])
    ann = r * 3 * 365 * 100
    log("%s 資金費年化 %.1f%%" % (sym, ann))
    if ann >= FUND_HI or ann <= FUND_LO:
        who = "多單付費給空單（做空永續+買現貨可收費）" if ann > 0 else "空單付費給多單（擁擠的是空方）"
        text = "**FUNDING｜%s 資金費極端 💸**\n目前年化 %+.1f%%：%s\n⚠ %s" % (sym, ann, who, HIST["FUNDING"])
        send("FUNDING", sym, text, ["info", "", "", "", "", "年化%.1f%%" % ann], state, dry)


def one_round(ex_f, ex_s, dry):
    load_dotenv()
    state = load_state()
    off = {x.strip() for x in os.environ.get("RADAR_DISABLE", "").split(",") if x.strip()}   # 例:FLUSH,FUNDING,SETUP
    only = {x.strip() for x in os.environ.get("RADAR_ONLY", "").split(",") if x.strip()}
    chk = [("SETUP", check_symbol, ex_f), ("FLUSH", check_flush, ex_s), ("FUNDING", check_funding, ex_f)]
    chk = [(k, fn, ex) for k, fn, ex in chk if k not in off and (not only or k in only)]
    if os.environ.get("RADAR_AUTOTRADE") == "1":
        try:
            import radar_autotrade as at
            at.settle()
        except Exception as e:
            log("AUTOTRADE_ERR settle %s" % str(e)[:160])
    for sym in SYMBOLS:
        for _k, fn, ex in chk:
            try:
                fn(sym, ex, state, dry)
            except Exception as e:
                log("ERR %s %s: %s" % (fn.__name__, sym, str(e)[:160]))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    load_dotenv()
    ex_f = ex_s = None
    only = os.environ.get("RADAR_ONLY", ""); off = os.environ.get("RADAR_DISABLE", "")
    if "SETUP" not in off and (not only or "SETUP" in only):     # 只有做多/做空偵測才需要 ccxt(吃記憶體)
        import ccxt
        ex_f = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True, "timeout": 15000})
        ex_s = ccxt.binance({"enableRateLimit": True, "timeout": 15000})
    if a.once:
        one_round(ex_f, ex_s, True); return
    log("=== radar start (每%ds) ===" % EVERY)
    t0 = time.time()
    while time.time() - t0 < MAX_RUNTIME:
        one_round(ex_f, ex_s, False)
        time.sleep(EVERY)
    log("已跑滿 %.0fh,優雅退出讓看門狗重啟" % (MAX_RUNTIME / 3600))


if __name__ == "__main__":
    main()
