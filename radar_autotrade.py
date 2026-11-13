# -*- coding: utf-8 -*-
"""radar_autotrade.py — H9 自建模擬倉（純模擬，不碰任何交易所帳戶）。規則見 HYPOTHESES.md 的 H9，登記後不得更改。
由 signal_radar.py 在 RADAR_AUTOTRADE=1 時呼叫：can_open()/on_signal() 開新單、settle() 每輪推進掛單/持倉狀態。
advance() 是「成交→停損/停利→到期」的唯一判斷函式：真單與 forward_test_h9.py 的隨機對照單共用，確保規則一模一樣。"""
import os, sys, json, time, datetime, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import risk_sizer as rs

STATE = os.path.join(HERE, "autotrade_state.json")
START_EQUITY, RISK_PCT, MAX_LEV = 1000.0, 1.0, 5.0
FEE_RT, STOP_SLIP = 0.0010, 0.0002
TTL_BARS, MAX_HOLD_BARS = 8, 288          # 掛單 2 小時；持倉最長 72 小時（15m×288）
MAX_PER_DAY, DAY_STOP_R = 2, -3.0
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


# ───────── 共用模擬核心（真單與對照單共用）─────────
def _slip_sl(t):
    return t["sl"] * ((1 - STOP_SLIP) if t["side"] == "long" else (1 + STOP_SLIP))


def _calc_net(t, px):
    risk_px = abs(t["entry"] - t["sl"])
    gross = ((px - t["entry"]) if t["side"] == "long" else (t["entry"] - px)) / risk_px
    return gross - FEE_RT / (risk_px / t["entry"]), gross


def _finish(t, ms, px, reason):
    net, gross = _calc_net(t, px)
    t.update(status="CLOSED", exit_ms=ms, exit_px=px, exit_reason=reason, net_R=round(net, 4), gross_R=round(gross, 4))


def advance(t, bar):
    """用一根『已收盤』15m K 棒推進一筆單。回傳事件字串或 None：FILLED / CANCELLED / CLOSED。"""
    ms, o, h, l, c = bar
    long = t["side"] == "long"
    ev = None
    t["last_ms"] = ms
    if t["status"] == "PENDING":
        t["bars"] += 1
        if (l <= t["entry"]) if long else (h >= t["entry"]):
            t["status"] = "OPEN"; t["fill_ms"] = ms; t["held"] = 0
            ev = "FILLED"
            if (l <= t["sl"]) if long else (h >= t["sl"]):          # 成交那根只檢查停損（保守）
                _finish(t, ms, _slip_sl(t), "SL"); return "CLOSED"
            return ev
        if t["bars"] >= TTL_BARS:
            t["status"] = "CANCELLED"; t["exit_reason"] = "UNFILLED"
            return "CANCELLED"
        return None
    # OPEN
    t["held"] += 1
    sl_hit = (l <= t["sl"]) if long else (h >= t["sl"])
    tp_hit = (h >= t["tp"]) if long else (l <= t["tp"])
    if sl_hit:                                                      # 同根都碰到以停損計
        _finish(t, ms, _slip_sl(t), "SL"); return "CLOSED"
    if tp_hit:
        _finish(t, ms, t["tp"], "TP"); return "CLOSED"
    if t["held"] >= MAX_HOLD_BARS:                                  # 72 小時到期：收盤價市價出場
        _finish(t, ms, c * ((1 - STOP_SLIP) if long else (1 + STOP_SLIP)), "TIME"); return "CLOSED"
    return None


def sim_trade(bars, side, entry, sl, tp):
    """對照單用：給一串已收盤 K 棒（signal 之後的），回傳 (status, net_R)。status: CLOSED / CANCELLED / PENDING / OPEN。"""
    t = dict(side=side, entry=entry, sl=sl, tp=tp, status="PENDING", bars=0, held=0, last_ms=0,
             fill_ms=None, exit_ms=None, exit_px=None, exit_reason=None, net_R=None)
    for b in bars:
        advance(t, b)
        if t["status"] in ("CLOSED", "CANCELLED"):
            break
    return t["status"], t["net_R"]


# ───────── 開單 / 結算 ─────────
def can_open(st=None):
    """雷達發通知前先問：現在能不能開新單（有進行中、當日上限、當日停手都回 False）。"""
    st = st or load()
    if _active(st):
        return False
    today = _day(int(time.time() * 1000))
    if len([t for t in st["trades"] if _day(t["created_ms"]) == today]) >= MAX_PER_DAY:
        return False
    r_today = sum(t["net_R"] for t in st["trades"] if t["status"] == "CLOSED" and _day(t["exit_ms"]) == today)
    return r_today > DAY_STOP_R


def on_signal(kind, sym, side, lv, sig_open_ms, sig_close=None):
    st = load()
    if not can_open(st):
        _log("SKIP %s %s" % (kind, sym)); return None
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
             sig_close=float(sig_close) if sig_close else None,
             last_ms=int(sig_open_ms), bars=0, held=0, created_ms=int(time.time() * 1000), status="PENDING",
             fill_ms=None, exit_ms=None, exit_px=None, exit_reason=None, net_R=None)
    st["next_id"] += 1
    st["trades"].append(t)
    save(st)
    _log("PLACED #%d %s %s %s entry %.4f" % (t["id"], kind, sym, side, e))
    msg = ("🧪 **模擬倉掛單 #%d｜%s %s %s**（H9 前瞻實驗，虛擬資金）\n"
           "限價 %s｜停損 %s｜停利 %s｜R:R %.2f｜風險 $%.2f｜槓桿 %gx\n2 小時內沒成交就取消；持倉最長 72 小時。"
           % (t["id"], kind, sym, "做多" if side == "long" else "做空", _px(e), _px(s), _px(tp),
              t["rr"], t["risk_amt"], lev))
    notify(msg)
    return msg


def _klines(sym, start_ms):
    url = "https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&startTime=%d&limit=500" % (sym.replace("/", ""), start_ms)
    req = urllib.request.Request(url, headers={"User-Agent": "FOX-radar/1.0"})
    k = json.loads(urllib.request.urlopen(req, timeout=15).read())
    now = int(time.time() * 1000)
    return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in k if int(x[0]) + BAR_MS <= now]


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
    for bar in bars:
        if bar[0] <= t["last_ms"]:
            continue
        ev = advance(t, bar)
        name = "做多" if long else "做空"
        if ev == "FILLED":
            _log("FILLED #%d" % t["id"])
            notify("📥 **模擬倉成交 #%d｜%s %s**，成交價 %s" % (t["id"], t["symbol"], name, _px(t["entry"])))
        if ev == "CANCELLED":
            _log("CANCELLED #%d" % t["id"])
            notify("⌛ **模擬倉取消 #%d｜%s**：2 小時內沒成交（不計入樣本）" % (t["id"], t["symbol"])); break
        if t["status"] == "CLOSED":
            st["equity"] = round(st["equity"] + t["net_R"] * t["risk_amt"], 4)
            _log("CLOSED #%d %s" % (t["id"], t["exit_reason"]))       # log 不寫 R，守 40 筆前不看績效
            notify("🏁 **模擬倉結束 #%d｜%s %s**（%s）\n（40 筆前不顯示損益，守預先登記規則。）"
                   % (t["id"], t["symbol"], name, {"SL": "觸及停損", "TP": "觸及停利", "TIME": "72 小時到期出場"}[t["exit_reason"]]))
            break
    save(st)
