# -*- coding: utf-8 -*-
"""trade_desk.py — 進場參謀台（Demo 假錢專用）。
計算機（部位/槓桿/爆倉距離/手續費換算R）＋ 雷達訊號 ＋ 一鍵送 Demo 三張單（進場/停損/停利）＋ 自動日誌。
啟動：.venv\\Scripts\\python.exe -m streamlit run trade_desk.py --server.address 127.0.0.1 --server.port 8502
★ 這頁只會用 --testnet（幣安 Demo 假錢）下單，沒有真錢選項。"""
import os, sys, csv, json, subprocess, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE); sys.path.insert(0, HERE)
import pandas as pd
import streamlit as st
import risk_sizer as rs

JOURNAL, ALERTS = "desk_journal.csv", "radar_alerts.csv"
FEE_RT = 0.0010   # 吃單來回 0.10%

st.set_page_config(page_title="進場參謀台", page_icon="🦊", layout="wide")
st.title("🦊 進場參謀台")
st.caption("Demo（假錢）專用。幫你算部位、守護欄、記日誌；不保證賺錢。"
           "歷史回測：Setup C −0.021R、H4 空頭鏡像 −0.049R、H5 −0.243R、H6 −0.028R（扣費後都沒有優勢）。")


def paper_equity():
    try:
        return float(json.load(open("paper_manual_state.json", encoding="utf-8"))["equity"])
    except Exception:
        return 600.0


def px(x):
    return "{:,.2f}".format(x) if abs(x) >= 100 else ("{:,.4f}".format(x) if abs(x) >= 1 else "{:.6f}".format(x))


# ── 1. 雷達訊號 ──────────────────────────────────────────────
st.subheader("1. 雷達訊號")
if os.path.exists(ALERTS):
    al = pd.read_csv(ALERTS, encoding="utf-8-sig").tail(8).iloc[::-1].reset_index(drop=True)
    st.dataframe(al, use_container_width=True, hide_index=True)
    trade_rows = al[al["side"].isin(["long", "short"])]
    if len(trade_rows):
        pick = st.selectbox("把哪一筆帶入計算機？", range(len(trade_rows)),
                            format_func=lambda i: "%s %s %s @%s" % (trade_rows.iloc[i]["time"], trade_rows.iloc[i]["symbol"],
                                                                   trade_rows.iloc[i]["side"], trade_rows.iloc[i]["entry"]))
        if st.button("帶入下面的計算機"):
            r = trade_rows.iloc[pick]
            st.session_state.update(sym=r["symbol"], side=r["side"], entry=float(r["entry"]),
                                    sl=float(r["sl"]), tp=float(r["tp"]))
else:
    st.info("還沒有雷達訊號（signal_radar.py 有符合條件才會寫入）。")

# ── 2. 計算機 ────────────────────────────────────────────────
st.subheader("2. 計算機與護欄")
c1, c2, c3 = st.columns(3)
sym = c1.selectbox("標的", ["BTC/USDT", "ETH/USDT"], index=["BTC/USDT", "ETH/USDT"].index(st.session_state.get("sym", "BTC/USDT")))
side = c1.radio("方向", ["long", "short"], index=["long", "short"].index(st.session_state.get("side", "long")), horizontal=True)
entry = c2.number_input("進場價", value=float(st.session_state.get("entry", 75000.0)), format="%.4f")
sl = c2.number_input("停損價", value=float(st.session_state.get("sl", 76000.0 if st.session_state.get("side") == "short" else 74000.0)), format="%.4f")
tp = c2.number_input("停利價", value=float(st.session_state.get("tp", 72000.0 if st.session_state.get("side") == "short" else 78000.0)), format="%.4f")
equity = c3.number_input("權益（預設=模擬倉權益）", value=paper_equity(), format="%.2f")
risk = c3.slider("單筆風險 %", 0.25, 1.0, 1.0, 0.25)
lev = c3.slider("槓桿 x", 1, 20, 5)

r = rs.compute(equity, risk, side, entry, sl, tp, lev)
guard_fail = True
if r is None:
    st.error("停損距離為 0，無法計算。")
else:
    stop_pct = r["stop_dist"]
    m = st.columns(5)
    m[0].metric("部位數量", "%.4f" % r["qty"])
    m[1].metric("名目金額", "$%.0f" % r["notional"])
    m[2].metric("保證金", "$%.0f" % r["margin"])
    m[3].metric("爆倉價", px(r["liq"]), "距進場 %.1f%%" % (r["liq_dist"] * 100), delta_color="off")
    m[4].metric("手續費 = 幾 R", "%.2f R" % (FEE_RT / stop_pct), "停損距離 %.2f%%" % (stop_pct * 100), delta_color="off")
    checks = []
    if not (r["sl_ok"] and r["tp_ok"]):
        checks.append((False, "R1 停損/停利放錯邊（%s 應停損在%s、停利在%s）" % (
            side, "下" if side == "long" else "上", "上" if side == "long" else "下")))
    else:
        checks.append((True, "R1 停損/停利方向正確"))
    checks.append((r["stop_first"], "R3 停損先於爆倉" if r["stop_first"]
                   else "R3 爆倉比停損近（%.1f%% < %.1f%%）→ 降槓桿，否則是爆倉單" % (r["liq_dist"] * 100, stop_pct * 100)))
    checks.append((r["rr"] is not None and r["rr"] >= rs.RR_MIN,
                   "R4 賺賠比 %.2f %s" % (r["rr"], "≥ %.1f" % rs.RR_MIN if r["rr"] >= rs.RR_MIN else "< %.1f，別進" % rs.RR_MIN)))
    fee_r = FEE_RT / stop_pct
    checks.append((fee_r <= 0.25, "手續費 %.2fR %s" % (fee_r, "可接受" if fee_r <= 0.25 else "太高：停損太近，來回費用吃掉太多期望值")))
    checks.append((lev <= r["max_safe_lev"], "槓桿 %dx %s（撐 2 倍插針的上限 ≈ %.1fx）" % (
        lev, "在安全範圍" if lev <= r["max_safe_lev"] else "超過安全上限", r["max_safe_lev"])))
    for ok, msg in checks:
        (st.success if ok else st.warning)(("✅ " if ok else "🔴 ") + msg)
    guard_fail = not (r["sl_ok"] and r["tp_ok"] and r["stop_first"])

# ── 3. 送 Demo 單 ────────────────────────────────────────────
st.subheader("3. 送 Demo 單（假錢）")
reason = st.text_area("進場理由（必填，至少 10 個字；先寫下來才能下單）", placeholder="例如：4h 下跌、15m 反彈碰 EMA20 後收紅，停損放近期高點上方…")
confirm = st.checkbox("我確認這是幣安 Demo 假錢單，且已看過上面的護欄")
cA, cB = st.columns(2)


def run_order(live):
    cmd = [sys.executable, "live_order.py", "--symbol", sym, "--side", side, "--entry", str(entry), "--sl", str(sl),
           "--tp", str(tp), "--risk", str(risk), "--leverage", str(lev), "--equity", str(equity), "--testnet"]
    if live:
        cmd.append("--live")
    p = subprocess.run(cmd, input="YES\n" if live else None, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=90, cwd=HERE)
    return (p.stdout + p.stderr).strip(), p.returncode


if cA.button("預覽（只計算，不下單）", disabled=guard_fail):
    out, _ = run_order(False)
    st.code(out)
if cB.button("🧪 送出 Demo 三張單", type="primary", disabled=guard_fail or not confirm or len(reason.strip()) < 10):
    out, code = run_order(True)
    st.code(out)
    ok = ("三張單都送出成功" in out)
    new = not os.path.exists(JOURNAL)
    with open(JOURNAL, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "symbol", "side", "entry", "sl", "tp", "risk_pct", "leverage", "status", "reason"])
        w.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sym, side, entry, sl, tp, risk, lev,
                    "SENT" if ok else "FAILED/REFUSED", reason.strip().replace("\n", " ")])
    (st.success if ok else st.error)("已送出並記入日誌" if ok else "沒有送出（看上面的原因），已記入日誌")
if guard_fail:
    st.caption("護欄 R1/R3 沒過時，送單按鈕會停用（live_order.py 也會再擋一次）。")
if not confirm or len(reason.strip()) < 10:
    st.caption("勾選確認並寫下理由後，才能送單。")

# ── 4. 日誌 ──────────────────────────────────────────────────
st.subheader("4. 這個工具的下單日誌")
if os.path.exists(JOURNAL):
    st.dataframe(pd.read_csv(JOURNAL, encoding="utf-8-sig").iloc[::-1], use_container_width=True, hide_index=True)
else:
    st.caption("還沒有紀錄。")
