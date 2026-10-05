# -*- coding: utf-8 -*-
"""demo_mirror.py — 幣安 Demo(假錢)帳戶「固定規則」自動交易(見 HYPOTHESES.md 的 H3)。

只在 Demo 環境運作(強制 --testnet 路徑,端點不是 demo-fapi 就中止);不碰真實帳戶、不使用真實金鑰。
規則(登記後不得更改):
  ① _cnn_watch.log 最近2次檢查都是 TRIGGER 且 4h=up 信心≥90%
  ② _entry_flag.json 的 R:R ≥ 2.0
  ③ Demo 的 BTC 無持倉/無進場掛單/無殘留條件單
  ④ 距上次下單 ≥ 6 小時
下單 = live_order.main()(--from-flag --risk 1 --testnet --live,權益以本機模擬倉權益計)。
管理:進場單 >12h 未成交 → 連停損停利一併取消;平倉後殘留條件單 → 自動清理。

用法:
  python demo_mirror.py --once --dry   # 只跑一輪、只印「會怎麼做」,不下單
  python demo_mirror.py                # 常駐(每60秒一輪,滿6小時自行退出讓看門狗重啟)
"""
import warnings; warnings.filterwarnings("ignore")
import builtins, contextlib, csv, datetime, io, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)
import live_order as lo

SYMBOL_UI = "BTC/USDT"
SYMBOL = "BTC/USDT:USDT"
RR_MIN, CONF_MIN, CONSEC = 2.0, 90, 2
COOLDOWN_H, ENTRY_TTL_H = 6, 12
LOOP_SEC, MAX_RUNTIME = 60, 6 * 3600
CNN_LOG, FLAG, PAPER = "_cnn_watch.log", "_entry_flag.json", "paper_manual_state.json"
BOT_LOG, STATE = "demo_bot_log.csv", "demo_bot_state.json"
LINE_RE = re.compile(r"CNN 4h=(\w+)\((\d+)%\).*-> (TRIGGER|wait)")


def log_event(event, detail=""):
    new = not os.path.exists(BOT_LOG)
    with open(BOT_LOG, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "event", "detail"])
        w.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), event, detail])
    print("[%s] %s %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), event, detail), flush=True)


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"last_placed_ts": 0}


def save_state(s):
    json.dump(s, open(STATE, "w", encoding="utf-8"))


def recent_cnn_status(n):
    """回傳最近 n 筆 CNN 檢查 [(4h_cls, conf, trig_bool)],不足 n 筆回傳 []。"""
    try:
        lines = [l for l in open(CNN_LOG, encoding="utf-8").read().splitlines() if "CNN 4h=" in l]
    except Exception:
        return []
    out = []
    for l in lines[-n:]:
        m = LINE_RE.search(l)
        if m:
            out.append((m.group(1), int(m.group(2)), m.group(3) == "TRIGGER"))
    return out if len(out) == n else []


def paper_equity():
    try:
        return float(json.load(open(PAPER, encoding="utf-8"))["equity"])
    except Exception:
        return None


def place_via_tool(equity):
    """在行程內呼叫 live_order.main()(自動回答 YES;只用於 --testnet Demo)。回傳工具的完整輸出。"""
    old_argv, old_input = sys.argv, builtins.input
    sys.argv = ["live_order.py", "--from-flag", "--equity", "%.2f" % equity, "--risk", "1", "--testnet", "--live"]
    builtins.input = lambda prompt="": "YES"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            lo.main()
    except SystemExit:
        pass
    finally:
        sys.argv, builtins.input = old_argv, old_input
    return buf.getvalue()


def one_round(ex, dry=False):
    try:
        qty, reg, cond = lo.symbol_state(ex, SYMBOL)
    except Exception:       # Demo 讀取偶發失敗(只讀,重試安全)→ 等3秒重試一次
        time.sleep(3)
        qty, reg, cond = lo.symbol_state(ex, SYMBOL)
    now = time.time()

    # ① 進場單逾時未成交 → 取消進場單+其停損停利
    if qty == 0 and reg:
        oldest = min((o.get("timestamp") or now * 1000) for o in reg) / 1000.0
        if now - oldest > ENTRY_TTL_H * 3600:
            msg = "進場單已掛 %.1f 小時未成交,取消進場單與 %d 張條件單" % ((now - oldest) / 3600, len(cond))
            if dry:
                print("DRY:", msg); return
            for o in reg:
                ex.cancel_order(o["id"], SYMBOL)
            lo.cancel_conditionals(ex, SYMBOL, cond)
            log_event("TTL_CANCEL", msg); return

    # ② 平倉後殘留條件單 → 自動清理
    if qty == 0 and not reg and cond:
        msg = "偵測到 %d 張殘留條件單(無持倉、無進場單)" % len(cond)
        if dry:
            print("DRY:", msg, "→ 會取消"); return
        lo.cancel_conditionals(ex, SYMBOL, cond)
        log_event("CLEANUP", msg); return

    # ③ 已有持倉/進場單 → 不動(交給停損停利/進場單自己處理)
    if qty > 0 or reg:
        if dry:
            print("DRY: 目前有持倉(%s)或進場單(%d),不新增。" % (qty, len(reg)))
        return

    # ④ 判斷是否該下新單
    st = load_state()
    hrs = (now - st.get("last_placed_ts", 0)) / 3600
    if hrs < COOLDOWN_H:
        if dry: print("DRY: 冷卻中(距上次下單 %.1f 小時 < %d)" % (hrs, COOLDOWN_H))
        return
    status = recent_cnn_status(CONSEC)
    if not status or not all(c == "up" and conf >= CONF_MIN and trig for c, conf, trig in status):
        if dry: print("DRY: 條件① 未達成(最近%d次CNN: %s)" % (CONSEC, status))
        return
    try:
        flag = json.load(open(FLAG, encoding="utf-8"))
    except Exception:
        if dry: print("DRY: 沒有 _entry_flag.json")
        return
    rr = float(flag.get("rr") or 0)
    if flag.get("symbol") != SYMBOL_UI or flag.get("side") != "long" or rr < RR_MIN:
        if dry: print("DRY: 條件② 未達成(flag=%s, R:R=%.2f < %.1f)" % (flag, rr, RR_MIN))
        return
    eq = paper_equity()
    if not eq:
        log_event("SKIP", "讀不到本機模擬倉權益"); return
    if dry:
        print("DRY: 條件全部達成 → 會以 --from-flag 下單。flag=%s 權益=%.2f" % (flag, eq)); return
    out = place_via_tool(eq)
    ok = "三張單都送出成功" in out
    tail = " | ".join(l.strip() for l in out.strip().splitlines()[-4:])
    log_event("PLACED" if ok else "PLACE_FAILED", "flag=%s eq=%.2f :: %s" % (json.dumps(flag), eq, tail[:300]))
    if ok:
        st["last_placed_ts"] = now
        save_state(st)


_last_tick = [time.time()]


def _hang_guard(limit=300):
    """主迴圈超過 limit 秒沒動(卡死)→ 自殺退出,讓看門狗重啟。"""
    while True:
        time.sleep(30)
        if time.time() - _last_tick[0] > limit:
            try: log_event("HANG_EXIT", "主迴圈 %ds 無進展,強制退出" % limit)
            finally: os._exit(1)


def main():
    dry = "--dry" in sys.argv
    once = "--once" in sys.argv
    ex, err = lo.load_client(True)          # 強制 Demo
    if ex is None:
        print("❌ %s" % err); sys.exit(1)
    if "demo-fapi" not in str(ex.urls.get("api", {})).lower():
        print("❌ 安全檢查失敗:不是 Demo 端點,中止"); sys.exit(1)
    if not once:
        log_event("START", "demo_mirror 啟動(每%ds一輪)" % LOOP_SEC)
    started = time.time()
    if not once:
        import threading
        threading.Thread(target=_hang_guard, daemon=True).start()
    while True:
        _last_tick[0] = time.time()
        try:
            one_round(ex, dry)
        except Exception as e:
            log_event("ERROR", "%s: %s" % (type(e).__name__, re.sub(r"signature=\w+", "", str(e))[-220:]))
        if once:
            return
        if time.time() - started > MAX_RUNTIME:
            log_event("RESTART", "已跑滿%dh,優雅退出讓看門狗重啟" % (MAX_RUNTIME // 3600)); return
        time.sleep(LOOP_SEC)


if __name__ == "__main__":
    main()
