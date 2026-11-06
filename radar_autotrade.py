# -*- coding: utf-8 -*-
"""radar_autotrade.py — H8 自建模擬倉（純模擬，不碰任何交易所帳戶）。規則見 HYPOTHESES.md 的 H8，登記後不得更改。
由 signal_radar.py 在 RADAR_AUTOTRADE=1 時呼叫：on_signal() 開新單、settle() 每輪推進掛單/持倉狀態。"""
import os, sys, json, time, datetime, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import risk_sizer as rs

STATE = os.path.join(HERE, "autotrade_state.json")
START_EQUITY, RISK_PCT, MAX_LEV = 1000.0, 1.0, 5.0
FEE_RT, STOP_SLIP = 0.0010, 0.0002
TTL_BARS, MAX_PER_DAY, DAY_STOP_R = 8, 2, -3.0
BAR_MS = 15 * 60 * 1000


def _log(msg):
    line = "[%s] AUTOTRADE %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    try:
        with open(os.path.join(HERE, "radar.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"equity": START_EQUITY, "next_id": 1, "trades": []}


def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def notify(text):
    url = (os.environ.get("DISCORD_WEBHOOK_PAPER") or os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not url:
        return
    tw = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%m/%d %H:%M")
    body = ("🕒 %s（台灣時間）\n%s" % (tw, text))[:1900]
    try:
        req = urllib.request.Request(url, data=json.dumps({"content": body}).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "User-Agent": "FOX-radar/1.0"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        _log("DISCORD_FAIL %s" % type(e).__name__)


def _px(x):
    return "{:,.2f}".format(x) if abs(x) >= 100 else "{:,.4f}".format(x)


def _day(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")


def _active(st):
    return [t for t in st["trades"] if t["status"] in ("PENDING", "OPEN")]


def on_signal(kind, sym, side, lv, sig_open_ms):
    """雷達剛發出 SETUP 通知後呼叫。"""
    st = load()
    if _active(st):
        _log("SKIP busy %s %s" % (kind, sym)); return None
    today = _day(int(time.time() * 1000))
    if len([t for t in st["trades"] if _day(t["created_ms"]) == today]) >= MAX_PER_DAY:
        _log("SKIP daily max %s %s" % (kind, sym)); return None
    r_today = sum(t["net_R"] for t in st["trades"] if t["status"] == "CLOSED" and _day(t["exit_ms"]) == today)
    if r_today <= DAY_STOP_R:
        _log("SKIP day-stop %.2fR" % r_today); return None
    e, s, tp = float(lv["entry"]), float(lv["sl"]), float(lv["tp"])
    eq = st["equity"]
    r0 = rs.compute(eq, RISK_PCT, side, e, s, tp, MAX_LEV)
    if r0 is None:
        return None
    lev = max(1.0, min(MAX_LEV, float(int(r0["max_safe_lev"]))))
    r = rs.compute(eq, RISK_PCT, side, e, s, tp, lev)
    if not (r["sl_ok"] and r["tp_ok"] and r["stop_first"]):
        _log("SKIP guard %s %s" % (kind, sym)); return None
    t = dict(id=st["next_id"], kind=kind, symbol=sym, side=side, entry=e, sl=s, tp=tp, rr=round(r["rr"], 3),
             risk_amt=round(r["risk_amt"], 4), lev=lev, equity_at=round(eq, 2), sig_open_ms=int(sig_open_ms),
             last_ms=int(sig_open_ms), bars=0, created_ms=int(time.time() * 1000), status="PENDING",
             fill_ms=None, exit_ms=None, exit_px=None, exit_reason=None, net_R=None)
    st["next_id"] += 1
    st["trades"].append(t)
    save(st)
    _log("PLACED #%d %s %s %s entry %.4f" % (t["id"], kind, sym, side, e))
    msg = ("🧪 **模擬倉掛單 #%d｜%s %s %s**（H8 前瞻實驗，虛擬資金）\n"
           "限價 %s｜停損 %s｜停利 %s｜R:R %.2f｜風險 $%.2f（權益 $%.0f 的 1%%）｜槓桿 %gx\n2 小時內沒成交就取消。"
           % (t["id"], kind, sym, "做多" if side == "long" else "做空", _px(e), _px(s), _px(tp),
              t["rr"], t["risk_amt"], eq, lev))
    notify(msg)
    return msg


def _klines(sym, start_ms):
    url = "https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&startTime=%d&limit=100" % (sym.replace("/", ""), start_ms)
    req = urllib.request.Request(url, headers={"User-Agent": "FOX-radar/1.0"})
    k = json.loads(urllib.request.urlopen(req, timeout=15).read())
    now = int(time.time() * 1000)
    return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in k if int(x[0]) + BAR_MS <= now]


def _close(st, t, ms, px, reason):
    long = t["side"] == "long"
    risk_px = abs(t["entry"] - t["sl"])
    gross = ((px - t["entry"]) if long else (t["entry"] - px)) / risk_px
    net = gross - FEE_RT / (risk_px / t["entry"])
    t.update(status="CLOSED", exit_ms=ms, exit_px=px, exit_reason=reason, net_R=round(net, 4), gross_R=round(gross, 4))
    st["equity"] = round(st["equity"] + net * t["risk_amt"], 4)
    _log("CLOSED #%d %s net %.2fR eq %.2f" % (t["id"], reason, net, st["equity"]))
    notify("%s **模擬倉結束 #%d｜%s %s**（%s）\n出場 %s｜淨 **%+.2fR**（約 %+.2f USDT，含費用）\n虛擬權益：$%.2f"
           % ("✅" if net > 0 else "🔴", t["id"], t["symbol"], "做多" if long else "做空",
              {"SL": "觸及停損", "TP": "觸及停利"}[reason], _px(px), net, net * t["risk_amt"], st["equity"]))


def _slip_px(t):
    return t["sl"] * ((1 - STOP_SLIP) if t["side"] == "long" else (1 + STOP_SLIP))


def settle():
    """推進唯一一筆進行中的單。沒有就直接返回。"""
    st = load()
    act = _active(st)
    if not act:
        return
    t = act[0]
    long = t["side"] == "long"
    try:
        bars = _klines(t["symbol"], t["last_ms"] + 1)
    except Exception as e:
        _log("KLINE_FAIL %s" % type(e).__name__); return
    for ms, o, h, l, c in bars:
        if ms <= t["last_ms"]:
            continue
        t["last_ms"] = ms
        if t["status"] == "PENDING":
            t["bars"] += 1
            if (l <= t["entry"]) if long else (h >= t["entry"]):
                t["status"] = "OPEN"; t["fill_ms"] = ms
                _log("FILLED #%d" % t["id"])
                notify("📥 **模擬倉成交 #%d｜%s %s**，成交價 %s" % (t["id"], t["symbol"], "做多" if long else "做空", _px(t["entry"])))
                if (l <= t["sl"]) if long else (h >= t["sl"]):      # 成交那根只檢查停損（保守）
                    _close(st, t, ms, _slip_px(t), "SL"); break
            elif t["bars"] >= TTL_BARS:
                t["status"] = "CANCELLED"; t["exit_reason"] = "UNFILLED"
                _log("CANCELLED #%d" % t["id"])
                notify("⌛ **模擬倉取消 #%d｜%s**：2 小時內沒成交（不計入樣本）" % (t["id"], t["symbol"])); break
        else:
            sl_hit = (l <= t["sl"]) if long else (h >= t["sl"])
            tp_hit = (h >= t["tp"]) if long else (l <= t["tp"])
            if sl_hit:                                              # 同根都碰到以停損計
                _close(st, t, ms, _slip_px(t), "SL"); break
            if tp_hit:
                _close(st, t, ms, t["tp"], "TP"); break
    save(st)
