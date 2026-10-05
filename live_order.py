# -*- coding: utf-8 -*-
"""live_order.py — 真實幣安合約下單小工具(限價進場 + 停損停利自動掛)
================================================================
給你「不用一直盯盤」的方式:你打一行指令,過完護欄+確認後,
程式幫你在幣安【真實帳戶】掛好:
  · 限價進場單(GTC,價到才成交,不是追價市價)
  · 停損單(STOP_MARKET, reduceOnly, 價格打到自動市價平倉)
  · 停利單(TAKE_PROFIT_MARKET, reduceOnly, 價格打到自動市價平倉)
掛完之後完全不用管,交給幣安自己的系統執行,跟模擬倉掛單邏輯一樣。

★ 安全設計(照你的要求,比你原始需求再嚴一點):
  1. DRY_RUN 預設 True —— 不加 --live 絕對不會送出真實訂單,只印出計算結果。
  2. 即使加了 --live,送單前還會要你手動輸入 "YES" 二次確認(防手滑/防程式bug)。
  3. 單筆風險硬性上限 1%(超過直接報錯擋掉,不接受參數覆蓋)。
  4. 停損必須在爆倉價之前(複用 risk_sizer.compute 的 R3 護欄邏輯),擋不住就拒絕下單。
  5. 自動抓市場精度(stepSize)與最低名目金額(minNotional),算出的部位不合規會報錯,不會自動硬湊。
  6. API金鑰只從 .env 讀,絕不印在畫面/log上。

★ 這支工具由 Claude 撰寫,但【真實下單那個動作(--live 執行)必須由你自己在自己電腦上操作】,
  Claude 不會、也不能代你執行 --live 這個指令 —— 這是刻意的設計,不是忘記做。

用法:
  python live_order.py --symbol ETH/USDT --side long --entry 3200 --sl 3100 --tp 3400 --risk 1
      → 預設 DRY_RUN,只算給你看,什麼都不會送出

  python live_order.py --symbol ETH/USDT --side long --entry 3200 --sl 3100 --tp 3400 --risk 1 --live
      → 真的送單(你自己執行這行,會再跳出 YES 確認)

首次測試(不需要真實金鑰也能跑完整個計算流程,只是抓不到真實餘額時用 --equity 手動帶入):
  python live_order.py --symbol BTC/USDT --side long --entry 80500 --sl 79300 --tp 86000 --risk 1 --equity 150
"""
import argparse
import json
import os
import sys

import ccxt
from dotenv import load_dotenv

import risk_sizer as rs

RISK_HARD_CAP = 1.0   # 單筆風險硬上限(%),不接受覆蓋更高
LOG_CSV = "live_order_log.csv"
LOG_DEMO = "live_order_log_demo.csv"   # --testnet(Demo模擬環境)的紀錄另存,避免跟真實下單紀錄混在一起
FLAG_CANDIDATES = ["_entry_flag.json", "_setup_flag.json"]  # --from-flag 依序找,抓到第一個存在的


def read_flag(path):
    """讀訊號偵測器寫的JSON(symbol/side/entry/sl/tp/rr),不用手動轉抄數字。"""
    if path:
        candidates = [path]
    else:
        candidates = FLAG_CANDIDATES
    for p in candidates:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            print("📄 讀到訊號檔 %s: %s" % (p, d))
            return d
    print("❌ 找不到任何訊號檔(%s),目前可能沒有觸發中的訊號。" % ", ".join(FLAG_CANDIDATES))
    sys.exit(1)


def load_client(testnet=False):
    load_dotenv()
    if testnet:
        # 測試網(假錢):用獨立的 TESTNET_API_KEY / TESTNET_API_SECRET,不會碰到真實金鑰
        key = os.getenv("TESTNET_API_KEY")
        secret = os.getenv("TESTNET_API_SECRET")
        if not key or not secret:
            return None, "找不到 .env 裡的 TESTNET_API_KEY / TESTNET_API_SECRET(幣安 Demo Trading 模擬環境金鑰,假錢)"
    else:
        # 沿用專案既有的 .env 變數名稱(跟 dashboard.py get_auth_exchange() 同一組),不用使用者重填
        key = os.getenv("API_KEY") or os.getenv("BINANCE_API_KEY")
        secret = os.getenv("API_SECRET") or os.getenv("BINANCE_API_SECRET")
        if not key or not secret:
            return None, "找不到 .env 裡的 API_KEY / API_SECRET(DRY_RUN 模式不需要這組也能跑)"
    # 用 binanceusdm(USDS-M期貨專用類別)而非通用 binance():
    # 通用類別的 load_markets 預設會一併查現貨/槓桿市場,只開期貨權限的金鑰會被擋(-2015)。
    ex = ccxt.binanceusdm({
        "apiKey": key, "secret": secret,
        "options": {"fetchCurrencies": False},
        "enableRateLimit": True,
        "timeout": 15000,
    })
    if testnet:
        # ccxt 已停用期貨測試網(sandbox),改用幣安 Demo Trading(demo-fapi.binance.com,假錢)
        ex.enable_demo_trading(True)
        # 保險:確認真的指向模擬環境,否則中止(避免任何誤連真實環境)
        urls = str(ex.urls.get("api", {})).lower()
        if "demo-fapi" not in urls and "testnet" not in urls:
            return None, "安全檢查失敗:--testnet 模式沒有指向模擬環境端點,已中止(不會送任何訂單)"
    return ex, None


def get_equity(ex, manual_equity):
    if manual_equity is not None:
        return manual_equity
    if ex is None:
        return None
    bal = ex.fetch_balance()
    return float(bal["USDT"]["total"])


def public_client():
    """市場規則(最低量/精度)是公開資料,不需要金鑰就能查,DRY_RUN也能完整驗證。"""
    return ccxt.binanceusdm({"options": {"fetchCurrencies": False}, "enableRateLimit": True})


def check_market_limits(pub, symbol, qty, notional):
    markets = pub.load_markets()
    m = markets[symbol]
    min_notional = (m.get("limits", {}).get("cost", {}) or {}).get("min")
    min_qty = (m.get("limits", {}).get("amount", {}) or {}).get("min")
    if min_notional and notional < min_notional:
        return False, "名目金額 %.4f 低於交易所最低門檻 %.2f —— 這組風險%%/金額太小,無法下單" % (notional, min_notional)
    if min_qty and qty < min_qty:
        return False, "數量 %.6f 低於最低下單量 %.6f" % (qty, min_qty)
    return True, "通過交易所最低限制檢查(min_notional=%.1f)" % (min_notional or -1)


def round_to_precision(pub, symbol, qty, price):
    markets = pub.load_markets()
    qty_r = float(pub.amount_to_precision(symbol, qty))
    price_r = float(pub.price_to_precision(symbol, price))
    return qty_r, price_r


def symbol_state(ex, symbol):
    """回傳(持倉量, 進場等一般掛單list, 條件單list)。
    條件單=停損/停利這類 reduceOnly 觸發單。【實測:幣安不會在平倉後自動取消它們,也沒有OCO】,
    殘留的停損單可能誤砍下一筆新倉位,所以必須主動偵測/清理。"""
    pos = [p for p in ex.fetch_positions([symbol]) if p.get("contracts")]
    qty = sum(abs(float(p["contracts"])) for p in pos)
    reg = ex.fetch_open_orders(symbol)
    cond = ex.fetch_open_orders(symbol, params={"trigger": True})
    return qty, reg, cond


def cancel_conditionals(ex, symbol, conds):
    for o in conds:
        ex.cancel_order(o["id"], symbol, params={"trigger": True})


def run_cleanup(args):
    sym = args.symbol
    if not sym:
        print("❌ --cleanup 需要 --symbol(例如 --symbol BTC/USDT)"); sys.exit(1)
    if ":" not in sym and "/" in sym:
        sym = sym + ":" + sym.split("/")[1]
    ex, err = load_client(args.testnet)
    if ex is None:
        print("❌ %s" % err); sys.exit(1)
    qty, reg, cond = symbol_state(ex, sym)
    where = "測試網(假錢)" if args.testnet else "【真實】帳戶"
    print("%s %s 目前狀態: 持倉=%s  進場等一般掛單=%d  條件單(停損/停利)=%d" % (where, sym, qty, len(reg), len(cond)))
    if qty > 0 or reg:
        print("ℹ 有進行中的持倉或進場單,條件單可能仍在保護中,不清理。"); return
    if not cond:
        print("✅ 沒有殘留的條件單,不用清理。"); return
    for o in cond:
        print("  殘留條件單: id=%s %s 觸發價=%s" % (o["id"], o.get("side"), o.get("stopPrice") or o.get("triggerPrice")))
    if input("沒有持倉也沒有進場單,這 %d 張是殘留單。要取消嗎？輸入大寫 YES 確認: " % len(cond)) != "YES":
        print("已取消,沒有動任何訂單。"); return
    cancel_conditionals(ex, sym, cond)
    print("✅ 已取消 %d 張殘留條件單。" % len(cond))


def log_order(row, log_path=None):
    log_path = log_path or LOG_CSV
    import csv
    new = not os.path.exists(log_path)
    with open(log_path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new:
            w.writeheader()
        w.writerow(row)


def main():
    ap = argparse.ArgumentParser(description="真實幣安合約下單(限價進場+OCO停損停利),預設DRY_RUN")
    ap.add_argument("--symbol", help="例如 BTC/USDT(有--from-flag時可省略)")
    ap.add_argument("--side", choices=["long", "short"], help="(有--from-flag時可省略)")
    ap.add_argument("--entry", type=float, help="(有--from-flag時可省略)")
    ap.add_argument("--sl", type=float, help="(有--from-flag時可省略)")
    ap.add_argument("--tp", type=float, help="(有--from-flag時可省略)")
    ap.add_argument("--risk", type=float, default=1.0, help="單筆風險%% (硬上限1%%,預設1%%)")
    ap.add_argument("--leverage", type=float, default=5.0)
    ap.add_argument("--equity", type=float, default=None, help="手動帶入權益(沒連線/想自訂測試時用)")
    ap.add_argument("--live", action="store_true", help="真的送出訂單(預設不加=只算不下單)")
    ap.add_argument("--cleanup", action="store_true",
                    help="取消指定交易對『沒有持倉也沒有進場單』時殘留的停損/停利條件單(幣安平倉後不會自動取消)")
    ap.add_argument("--testnet", action="store_true",
                    help="改連幣安期貨【測試網】(假錢),先驗證三張單(進場/停損/停利)的完整下單流程,不動真實資金")
    ap.add_argument("--from-flag", nargs="?", const="", default=None,
                    help="直接讀訊號偵測器寫的檔案,不用手動打symbol/entry/sl/tp。"
                         "可指定檔名,或留空自動找 _entry_flag.json / _setup_flag.json")
    args = ap.parse_args()

    if args.cleanup:
        run_cleanup(args)
        return

    if args.from_flag is not None:
        d = read_flag(args.from_flag or None)
        args.symbol = args.symbol or d.get("symbol")
        args.side = args.side or d.get("side")
        args.entry = args.entry if args.entry is not None else float(d["entry"])
        args.sl = args.sl if args.sl is not None else float(d["sl"])
        args.tp = args.tp if args.tp is not None else float(d["tp"])

    # binanceusdm(USDS-M期貨專用類別)的交易對格式是 BTC/USDT:USDT,自動轉換使用者/訊號檔給的 BTC/USDT
    if args.symbol and ":" not in args.symbol and "/" in args.symbol:
        args.symbol = args.symbol + ":" + args.symbol.split("/")[1]

    missing = [k for k in ("symbol", "side", "entry", "sl", "tp")
              if getattr(args, k) is None]
    if missing:
        print("❌ 缺少必要參數: %s (用 --from-flag 自動帶入,或手動補齊這些參數)" % ", ".join(missing))
        sys.exit(1)

    print("=" * 60)
    if args.live and args.testnet:
        mode = "🧪 TESTNET LIVE(測試網假錢下單)"
    elif args.live:
        mode = "🔴 LIVE(真實下單)"
    else:
        mode = "🟢 DRY_RUN(只計算,不下單)" + (" [連測試網]" if args.testnet else "")
    print("live_order.py — %s 模式" % mode)
    print("=" * 60)

    # R2 護欄:風險硬上限,不接受覆蓋
    if args.risk > RISK_HARD_CAP:
        print("❌ 拒絕:風險 %.2f%% 超過硬上限 %.1f%%,這個工具不接受更高的設定。" % (args.risk, RISK_HARD_CAP))
        sys.exit(1)

    ex, err = load_client(args.testnet)
    if err:
        print("ℹ %s" % err)
    pub = public_client()   # 市場規則查詢不需要金鑰,DRY_RUN也能完整驗證精度/最低限制

    equity = get_equity(ex, args.equity)
    if equity is None:
        print("❌ 抓不到權益,也沒用 --equity 手動帶入,無法繼續。")
        sys.exit(1)

    # 複用 risk_sizer 的護欄邏輯(R2風險/R3停損先於爆倉/R4賺賠比)
    r = rs.compute(equity, args.risk, args.side, args.entry, args.sl, args.tp, args.leverage)
    if r is None:
        print("❌ 停損距離為0,無法計算。")
        sys.exit(1)

    print("\n--- 護欄計算結果 ---")
    print("權益: %.2f USDT" % equity)
    print("方向: %s   進場: %g   停損: %g   停利: %g   槓桿: %gx" % (
        args.side, args.entry, args.sl, args.tp, args.leverage))
    print("風險金額: %.4f USDT (%.2f%%)" % (r["risk_amt"], args.risk))
    print("部位數量: %.6f   名目金額: %.4f USDT   保證金: %.4f USDT" % (r["qty"], r["notional"], r["margin"]))
    print("賺賠比(R:R): %.2f   爆倉價: %.4f" % (r["rr"], r["liq"]))

    if not r["stop_first"]:
        print("\n❌ 拒絕:停損價在爆倉價之後 —— 會先爆倉而非停損出場,這張單本質是爆倉單,不給下。")
        sys.exit(1)
    if r["rr"] < rs.RR_MIN:
        print("\n⚠ 警告:賺賠比 %.2f 低於建議下限 %.1f(R4),但不強制擋,你自己評估。" % (r["rr"], rs.RR_MIN))

    qty, entry_px = round_to_precision(pub, args.symbol, r["qty"], args.entry)
    _, sl_px = round_to_precision(pub, args.symbol, r["qty"], args.sl)
    _, tp_px = round_to_precision(pub, args.symbol, r["qty"], args.tp)
    ok, msg = check_market_limits(pub, args.symbol, qty, qty * entry_px)
    print("\n交易所限制檢查: %s" % msg)
    if not ok:
        print("❌ 拒絕下單:%s" % msg)
        sys.exit(1)

    print("\n--- 實際會送出的三張單(四捨五入到交易所精度後) ---")
    side_binance = "BUY" if args.side == "long" else "SELL"
    close_side = "SELL" if args.side == "long" else "BUY"
    print("1) 限價進場: %s %s qty=%.6f price=%.4f (GTC)" % (side_binance, args.symbol, qty, entry_px))
    print("2) 停損單  : %s %s qty=%.6f stopPrice=%.4f (STOP_MARKET, reduceOnly)" % (close_side, args.symbol, qty, sl_px))
    print("3) 停利單  : %s %s qty=%.6f stopPrice=%.4f (TAKE_PROFIT_MARKET, reduceOnly)" % (close_side, args.symbol, qty, tp_px))

    if not args.live:
        print("\n🟢 這是 DRY_RUN,以上訂單【沒有】送出。加上 --live 並自己執行才會真的下單。")
        return

    if ex is None:
        print("\n❌ --live 模式需要 .env 裡的真實 API 金鑰,目前讀不到,中止。")
        sys.exit(1)

    qty_pos, reg_open, cond_open = symbol_state(ex, args.symbol)
    if qty_pos > 0 or reg_open:
        print("\n❌ 拒絕:%s 已有進行中的持倉(%s)或進場掛單(%d 張),不疊單(R6 不加碼)。" % (args.symbol, qty_pos, len(reg_open)))
        sys.exit(1)
    if cond_open:
        print("\n⚠ 偵測到 %d 張【殘留】停損/停利條件單(沒有持倉也沒有進場單),下單前會先取消它們:" % len(cond_open))
        for o in cond_open:
            print("   id=%s %s 觸發價=%s" % (o["id"], o.get("side"), o.get("stopPrice") or o.get("triggerPrice")))

    print("\n" + ("🧪 即將送出訂單到幣安【測試網】(假錢)。" if args.testnet else "🔴 即將送出【真實】訂單到你的幣安帳戶。"))
    confirm = input("確定要繼續嗎？輸入大寫 YES 確認: ")
    if confirm != "YES":
        print("已取消,沒有送出任何訂單。")
        return

    entry_order = None
    sl_order = None
    try:
        if cond_open:
            cancel_conditionals(ex, args.symbol, cond_open)
            print("已取消 %d 張殘留條件單。" % len(cond_open))
        entry_order = ex.create_order(args.symbol, "limit", side_binance, qty, entry_px,
                                      {"timeInForce": "GTC"})
        sl_order = ex.create_order(args.symbol, "STOP_MARKET", close_side, qty, None,
                                   {"stopPrice": sl_px, "reduceOnly": True})
        tp_order = ex.create_order(args.symbol, "TAKE_PROFIT_MARKET", close_side, qty, None,
                                   {"stopPrice": tp_px, "reduceOnly": True})
        print("\n✅ 三張單都送出成功:")
        print("  進場單ID: %s" % entry_order.get("id"))
        print("  停損單ID: %s" % sl_order.get("id"))
        print("  停利單ID: %s" % tp_order.get("id"))
        log_order({
            "symbol": args.symbol, "side": args.side, "entry": entry_px, "sl": sl_px, "tp": tp_px,
            "qty": qty, "risk_pct": args.risk, "rr": round(r["rr"], 3),
            "entry_order_id": entry_order.get("id"), "sl_order_id": sl_order.get("id"),
            "tp_order_id": tp_order.get("id"),
        }, LOG_DEMO if args.testnet else LOG_CSV)
        print("\n已記錄到 %s" % (LOG_DEMO if args.testnet else LOG_CSV))
        print("ℹ 停損/停利是各自獨立的條件單(沒有OCO):任一邊觸發或你手動平倉後,剩下那張不會自動取消。"
              "交易結束後請執行: python live_order.py --cleanup --symbol %s%s" % (
                  args.symbol.split(":")[0], " --testnet" if args.testnet else ""))
    except Exception as e:
        print("\n❌ 下單時發生錯誤: %s" % e)
        # 回滾:進場單已送出、但後面的停損/停利失敗 → 盡量取消進場單,避免留下沒有停損保護的單
        if entry_order is not None and entry_order.get("id"):
            try:
                ex.cancel_order(entry_order["id"], args.symbol)
                print("↩ 已自動取消剛送出的進場單(避免留下沒有停損保護的單)。")
            except Exception as e2:
                print("⚠⚠ 無法自動取消進場單(可能已成交)!請【立刻】到幣安確認持倉,手動設定停損或平倉。(%s)" % e2)
        if sl_order is not None and sl_order.get("id"):
            try:
                ex.cancel_order(sl_order["id"], args.symbol, params={"trigger": True})
                print("↩ 已自動取消剛送出的停損單。")
            except Exception as e3:
                print("⚠ 無法自動取消停損單,請手動處理: %s" % e3)
        print("請自行到幣安 App/網頁確認目前訂單與持倉狀態,必要時手動取消殘留的單。")
        sys.exit(1)


if __name__ == "__main__":
    main()
