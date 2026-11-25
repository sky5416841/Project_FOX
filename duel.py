# -*- coding: utf-8 -*-
"""duel.py — 虛擬資金對決（Claude vs 使用者 vs 隨機交易者）。純娛樂＋檢討用，不碰任何交易所帳戶；規則見 DUEL.md。
指令：
  python duel.py status
  python duel.py order <user|claude> <BTC|ETH> <long|short> --sl 61300 [--tp 58800] [--limit 60500] [--risk 2] [--lev 10] --reason "..."
  python duel.py close <單號> --reason "..."
  python duel.py move  <單號> [--sl 新停損] [--tp 新停利] --reason "..."
成交與出場用幣安永續 1 分 K（只用下單之後、已收盤的 K 棒）判定。"""
import os, sys, json, time, random, argparse, datetime, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "duel_state.json")
PLAYERS = ["user", "claude", "random"]
NAME = {"user": "你", "claude": "Claude", "random": "隨機交易者"}
START_EQ, DAYS = 1000.0, 28
MAX_RISK, FEE_SIDE, SLIP = 5.0, 0.0005, 0.0002
MAINT, MAX_LEV = 0.005, 125.0
TTL_MS = 24 * 3600 * 1000
MIN = 60 * 1000
SYMS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}


def now_ms():
    return int(time.time() * 1000)


def tw(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone(datetime.timedelta(hours=8))).strftime("%m/%d %H:%M")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "FOX-duel/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def last_price(sym):
    return float(_get("https://fapi.binance.com/fapi/v1/ticker/price?symbol=%s" % SYMS[sym])["price"])


def klines(sym, interval, start_ms=None, limit=1500):
    url = "https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=%s&limit=%d" % (SYMS[sym], interval, limit)
    if start_ms is not None:
        url += "&startTime=%d" % start_ms
    return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in _get(url)]


def minute_bars(sym, start_ms, end_ms):
    """open_time >= start_ms 且在 end_ms 前已收盤的 1 分 K。"""
    out, cur = [], start_ms
    while cur + MIN <= end_ms:
        k = klines(sym, "1m", cur)
        if not k:
            break
        out += [b for b in k if b[0] + MIN <= end_ms]
        cur = k[-1][0] + MIN
        if len(k) < 1500:
            break
    return out


def load():
    return json.load(open(STATE, encoding="utf-8"))


def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def new_state(start):
    return dict(start_ms=start, end_ms=start + DAYS * 86400000, next_id=1, orders=[],
                equity={p: START_EQ for p in PLAYERS}, random_next_ms=start, seed=random.SystemRandom().randrange(10**9))


# ───────── 結算核心 ─────────
def close_trade(st, o, ms, px, why):
    sign = 1 if o["side"] == "long" else -1
    pnl = sign * (px - o["entry"]) * o["qty"] - FEE_SIDE * (o["entry"] + px) * o["qty"]
    o.update(status="CLOSED", exit_ms=ms, exit_px=round(px, 4), exit_why=why, pnl=round(pnl, 4))
    st["equity"][o["player"]] = round(st["equity"][o["player"]] + pnl, 4)


def stop_px(o):
    return o["sl"] * ((1 - SLIP) if o["side"] == "long" else (1 + SLIP))


def advance(st, o, bar):
    ms, op, h, l, c = bar
    long = o["side"] == "long"
    o["last_ms"] = ms
    if o["status"] == "PENDING":
        if ms - o["created_ms"] > TTL_MS:
            o.update(status="CANCELLED", exit_ms=ms, exit_why="24 小時未成交")
        elif o["kind"] == "stop":                                    # 突破單：往上衝過才做多／往下跌破才做空
            trig = o["trigger"]
            if (h >= trig) if long else (l <= trig):
                base = max(op, trig) if long else min(op, trig)      # 跳空越過掛單價就用開盤價
                o.update(status="OPEN", fill_ms=ms, entry=round(base * ((1 + SLIP) if long else (1 - SLIP)), 4))
                if (l <= o["sl"]) if long else (h >= o["sl"]):
                    close_trade(st, o, ms, stop_px(o), "停損")
        elif (l <= o["entry"]) if long else (h >= o["entry"]):
            o.update(status="OPEN", fill_ms=ms)
            if (l <= o["sl"]) if long else (h >= o["sl"]):           # 成交那根只檢查停損
                close_trade(st, o, ms, stop_px(o), "停損")
        return
    sl_hit = (l <= o["sl"]) if long else (h >= o["sl"])
    tp_hit = o["tp"] is not None and ((h >= o["tp"]) if long else (l <= o["tp"]))
    if sl_hit:                                                       # 同一根都碰到以停損計
        # 跳空穿過停損時用開盤價
        gap = (op < o["sl"]) if long else (op > o["sl"])
        close_trade(st, o, ms, op if gap else stop_px(o), "停損")
    elif tp_hit:
        close_trade(st, o, ms, o["tp"], "停利")


def settle_orders(st, until, player=None):
    live = [o for o in st["orders"] if o["status"] in ("PENDING", "OPEN") and (player is None or o["player"] == player)]
    for o in live:
        for bar in minute_bars(o["symbol"], o["last_ms"] + MIN, until):
            advance(st, o, bar)
            if o["status"] not in ("PENDING", "OPEN"):
                break


def run_random(st, until):
    """隨機交易者：開賽起每 24 小時一次機會；手上沒單就亂數開一張（標的/方向/風險亂數，停損=1.5×1小時ATR，停利=2R）。"""
    while st["random_next_ms"] <= min(until, st["end_ms"] - 86400000):
        t = st["random_next_ms"]
        settle_orders(st, t, "random")
        busy = any(o["player"] == "random" and o["status"] in ("PENDING", "OPEN") for o in st["orders"])
        if not busy:
            rng = random.Random("%d-%d" % (st["seed"], t))
            sym, side = rng.choice(["BTC", "ETH"]), rng.choice(["long", "short"])
            risk = rng.choice([1.0, 2.0, 3.0, 4.0, 5.0])
            hk = [b for b in klines(sym, "1h", t - 30 * 3600000, 40) if b[0] + 3600000 <= t][-14:]
            atr = sum(b[2] - b[3] for b in hk) / len(hk)
            m = klines(sym, "1m", (t // MIN) * MIN, 1)
            if not m:
                return
            px = m[0][4] * ((1 + SLIP) if side == "long" else (1 - SLIP))
            d = 1.5 * atr
            sl = px - d if side == "long" else px + d
            tp = px + 2 * d if side == "long" else px - 2 * d
            add_order(st, "random", sym, side, px, sl, tp, risk, None, "亂數", "market", (m[0][0]))
        st["random_next_ms"] = t + 86400000


def settle(st):
    until = min(now_ms(), st["end_ms"])
    run_random(st, until)
    settle_orders(st, until)
    if now_ms() >= st["end_ms"]:                                     # 期末強制平倉
        for o in st["orders"]:
            if o["status"] == "OPEN":
                last = klines(o["symbol"], "1m", st["end_ms"] - MIN, 1)[0][4]
                close_trade(st, o, st["end_ms"], last, "期末結算")
            elif o["status"] == "PENDING":
                o.update(status="CANCELLED", exit_ms=st["end_ms"], exit_why="期末取消")


# ───────── 下單 ─────────
def add_order(st, player, sym, side, entry, sl, tp, risk, lev, reason, kind, created):
    eq = st["equity"][player]
    if not (0 < risk <= MAX_RISK):
        raise ValueError("單筆風險要在 0～%.0f%% 之間" % MAX_RISK)
    if (side == "long" and not sl < entry) or (side == "short" and not sl > entry):
        raise ValueError("停損放錯邊（做多停損要在進場下方，做空在上方）")
    if tp is not None and ((side == "long" and not tp > entry) or (side == "short" and not tp < entry)):
        raise ValueError("停利放錯邊")
    if any(o["player"] == player and o["symbol"] == sym and o["status"] in ("PENDING", "OPEN") for o in st["orders"]):
        raise ValueError("%s 的 %s 已有進行中的單（每人每標的一次一張）" % (NAME[player], sym))
    dist = abs(entry - sl)
    per_unit = dist + FEE_SIDE * (entry + sl) + SLIP * sl           # 打到停損的總虧損（含手續費與滑價）
    qty = eq * risk / 100 / per_unit
    notional = qty * entry
    used = sum(o["margin"] for o in st["orders"] if o["player"] == player and o["status"] in ("PENDING", "OPEN"))
    safe_max = min(MAX_LEV, 1 / (dist / entry + MAINT) * 0.98)       # 爆倉價必須在停損之外
    if lev is None:
        lev = max(1.0, float(int(min(safe_max, max(1.0, notional / max(eq - used, 1e-9) * 2)))))
    if lev > MAX_LEV or lev < 1:
        raise ValueError("槓桿要在 1～%d 倍" % MAX_LEV)
    if lev > safe_max:
        raise ValueError("%gx 會讓爆倉價比停損先到；這個停損最多約 %.1fx" % (lev, safe_max))
    margin = notional / lev
    if margin > eq - used + 1e-9:
        raise ValueError("保證金不足：需要 %.1f，可用 %.1f（提高槓桿或降低風險）" % (margin, eq - used))
    o = dict(id=st["next_id"], player=player, symbol=sym, side=side, entry=round(entry, 4), sl=round(sl, 4),
             tp=None if tp is None else round(tp, 4), risk=risk, lev=lev, qty=qty, notional=round(notional, 2),
             margin=margin, equity_at=eq, kind=kind, reason=reason, created_ms=created,
             last_ms=(created // MIN) * MIN, status="OPEN" if kind == "market" else "PENDING",
             fill_ms=created if kind == "market" else None, exit_ms=None, exit_px=None, exit_why=None, pnl=None, notes=[])
    st["next_id"] += 1
    st["orders"].append(o)
    return o


# ───────── 顯示 ─────────
def describe(o):
    s = "#%d %s %s %s｜%s %.2f｜停損 %.2f｜停利 %s｜風險 %g%%｜%gx｜名目 $%.0f" % (
        o["id"], NAME[o["player"]], o["symbol"], "做多" if o["side"] == "long" else "做空",
        {"market": "市價", "limit": "限價", "stop": "突破"}[o["kind"]], o["entry"], o["sl"],
        "-" if o["tp"] is None else "%.2f" % o["tp"], o["risk"], o["lev"], o["notional"])
    st_ = {"PENDING": "掛單中", "OPEN": "持倉中", "CLOSED": "已平倉", "CANCELLED": "已取消"}[o["status"]]
    s += "｜" + st_
    if o["status"] == "CLOSED":
        s += "（%s %.2f，%+.2f USDT）" % (o["exit_why"], o["exit_px"], o["pnl"])
    return s


def show(st):
    print("對決期間：%s → %s（台灣時間）" % (tw(st["start_ms"]), tw(st["end_ms"])))
    px = {s: last_price(s) for s in SYMS}
    rows = []
    for p in PLAYERS:
        un = 0.0
        for o in st["orders"]:
            if o["player"] == p and o["status"] == "OPEN":
                sign = 1 if o["side"] == "long" else -1
                un += sign * (px[o["symbol"]] - o["entry"]) * o["qty"] - FEE_SIDE * (o["entry"] + px[o["symbol"]]) * o["qty"]
        rows.append((st["equity"][p] + un, p, st["equity"][p], un))
    for total, p, real, un in sorted(rows, reverse=True):
        print("  %-6s 權益 %8.2f（已實現 %8.2f｜未實現 %+7.2f）" % (NAME[p], total, real, un))
    print("現價 BTC %.1f｜ETH %.2f" % (px["BTC"], px["ETH"]))
    for o in st["orders"]:
        print("  " + describe(o))
        if o["player"] != "random":
            print("      理由：" + o["reason"])
            for n in o["notes"]:
                print("      %s %s" % (tw(n["ms"]), n["text"]))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    a = sub.add_parser("order")
    a.add_argument("player", choices=["user", "claude"]); a.add_argument("symbol", choices=list(SYMS))
    a.add_argument("side", choices=["long", "short"])
    a.add_argument("--sl", type=float, required=True); a.add_argument("--tp", type=float)
    a.add_argument("--limit", type=float); a.add_argument("--breakout", type=float); a.add_argument("--risk", type=float, default=2.0)
    a.add_argument("--lev", type=float); a.add_argument("--reason", required=True)
    c = sub.add_parser("close"); c.add_argument("id", type=int); c.add_argument("--reason", required=True)
    m = sub.add_parser("move"); m.add_argument("id", type=int); m.add_argument("--sl", type=float)
    m.add_argument("--tp", type=float); m.add_argument("--reason", required=True)
    args = ap.parse_args()

    if args.cmd == "init":
        if os.path.exists(STATE):
            sys.exit("已經開賽了（duel_state.json 已存在），不覆蓋。")
        st = new_state(now_ms()); settle(st); save(st); show(st); return
    st = load(); settle(st); save(st)
    if args.cmd == "status":
        show(st); return
    if now_ms() >= st["end_ms"]:
        sys.exit("比賽已結束。")
    t = now_ms()
    if args.cmd == "order":
        px = last_price(args.symbol)
        trigger = None
        if args.limit is not None and args.breakout is not None:
            sys.exit("--limit 和 --breakout 只能擇一。")
        if args.breakout is not None:
            if (args.side == "long" and args.breakout <= px) or (args.side == "short" and args.breakout >= px):
                sys.exit("突破單：做多要掛在現價 %.2f 上方，做空掛在下方。" % px)
            trigger, kind = args.breakout, "stop"
            entry = trigger * ((1 + SLIP) if args.side == "long" else (1 - SLIP))
        elif args.limit is not None:
            if (args.side == "long" and args.limit >= px) or (args.side == "short" and args.limit <= px):
                sys.exit("限價單要掛在比現價 %.2f 更好的價位；要立刻進場就不要給 --limit。" % px)
            entry, kind = args.limit, "limit"
        else:
            entry, kind = px * ((1 + SLIP) if args.side == "long" else (1 - SLIP)), "market"
        try:
            o = add_order(st, args.player, args.symbol, args.side, entry, args.sl, args.tp, args.risk, args.lev,
                          args.reason, kind, t)
            if trigger is not None:
                o["trigger"] = trigger
        except ValueError as e:
            sys.exit("拒絕：%s" % e)
        save(st); print("已記錄（%s）：%s" % (tw(t), describe(o))); return
    o = next((x for x in st["orders"] if x["id"] == args.id), None)
    if not o or o["status"] not in ("PENDING", "OPEN") or o["player"] == "random":
        sys.exit("找不到這張進行中的單。")
    if args.cmd == "close":
        if o["status"] == "PENDING":
            o.update(status="CANCELLED", exit_ms=t, exit_why="手動取消")
        else:
            px = last_price(o["symbol"])
            close_trade(st, o, t, px * ((1 - SLIP) if o["side"] == "long" else (1 + SLIP)), "手動平倉")
        o["notes"].append(dict(ms=t, text="平倉/取消：" + args.reason))
    else:
        px = last_price(o["symbol"])
        long = o["side"] == "long"
        if args.sl is not None:
            if (long and args.sl < o["sl"]) or (not long and args.sl > o["sl"]):
                sys.exit("拒絕：停損只能往減少虧損的方向移。")
            if o["status"] == "OPEN" and ((long and args.sl >= px) or (not long and args.sl <= px)):
                sys.exit("拒絕：新停損已越過現價 %.2f，要出場請用 close。" % px)
            o["notes"].append(dict(ms=t, text="停損 %.2f→%.2f：%s" % (o["sl"], args.sl, args.reason))); o["sl"] = args.sl
        if args.tp is not None:
            if (long and args.tp <= px) or (not long and args.tp >= px):
                sys.exit("拒絕：新停利已越過現價 %.2f。" % px)
            o["notes"].append(dict(ms=t, text="停利 %s→%.2f：%s" % (o["tp"], args.tp, args.reason))); o["tp"] = args.tp
        o["last_ms"] = max(o["last_ms"], (t // MIN) * MIN)
    save(st); print(describe(o))


if __name__ == "__main__":
    main()
