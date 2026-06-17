"""進場條件分析器：找出「哪種條件真的有正期望值」。

★ 2026-06 強化：以「扣手續費後的淨損益」做期望值判讀。
   trade_history 的 pnl 欄位是「毛利（僅價差）」，引擎在平倉時另外扣了
   開倉/平倉手續費（各 0.05%）與資金費，但那兩者沒寫進資料表。
   本腳本由 qty = pnl/價差 反推每筆名目，估回手續費，算出「淨損益」，
   才能回答真正的問題：扣費後到底是正是負。
   （資金費無法由現有欄位重建，故未計入；對只做多者通常是小額成本，
     代表「淨損益」其實還略為高估，結論偏樂觀。）
"""
import sqlite3

# 與 engine_core.py 同步（OPEN_FEE_RATE / CLOSE_FEE_RATE）
OPEN_FEE_RATE  = 0.0005
CLOSE_FEE_RATE = 0.0005

c = sqlite3.connect("fox_trading.db")
c.row_factory = sqlite3.Row
# 只取有 context 的交易（rsi 或 trend_gap 非 0）
rows = [dict(r) for r in c.execute(
    "SELECT * FROM trade_history WHERE (rsi!=0 OR trend_gap!=0)").fetchall()]


def _enrich_fees(r):
    """由 qty = pnl / 價差 反推名目，估回來回手續費，補上 net / fees 欄位。"""
    entry = r.get("entry_price") or 0.0
    exit_ = r.get("exit_price") or 0.0
    pnl   = r.get("pnl") or 0.0
    diff  = (exit_ - entry) if r.get("side") == "Long" else (entry - exit_)
    if abs(diff) < 1e-9 or entry <= 0:
        # 價差過小無法可靠反推 qty，退回毛利（罕見）
        r["fees"] = 0.0
        r["net"]  = pnl
        return r
    qty       = pnl / diff                      # 恆正
    open_fee  = abs(qty) * entry * OPEN_FEE_RATE
    close_fee = abs(qty) * exit_ * CLOSE_FEE_RATE
    fees      = open_fee + close_fee
    r["fees"] = fees
    r["net"]  = pnl - fees
    return r


rows = [_enrich_fees(r) for r in rows]


def stat(label, subset):
    n = len(subset)
    if n == 0:
        print(f"{label:<22} 筆數=0")
        return
    wins   = sum(1 for r in subset if r["net"] > 0)   # 勝率改以「扣費後」為準
    wr     = wins / n * 100
    g_avg  = sum(r["pnl"] for r in subset) / n
    n_avg  = sum(r["net"] for r in subset) / n
    n_tot  = sum(r["net"] for r in subset)
    flag   = "⚠負" if n_avg < 0 else "  "
    print(f"{label:<22} 筆數={n:<4} 淨勝率={wr:5.1f}%  毛均={g_avg:+7.1f}  "
          f"淨均={n_avg:+7.1f}{flag}  淨總={n_tot:+9.1f}")


def bucket(name, subset, key, edges, labels):
    print(f"\n── {name} ──")
    for i, lab in enumerate(labels):
        lo = edges[i]; hi = edges[i + 1]
        sub = [r for r in subset if lo <= r[key] < hi]
        stat(lab, sub)


longs  = [r for r in rows if r["side"] == "Long"]
shorts = [r for r in rows if r["side"] == "Short"]

print("=" * 78)
print(f"總樣本（有context）：{len(rows)} 筆")
print("=" * 78)

# ── 頭條：扣費後到底有沒有 edge ───────────────────────────────────────────
if rows:
    g_tot = sum(r["pnl"]  for r in rows)
    f_tot = sum(r["fees"] for r in rows)
    n_tot = sum(r["net"]  for r in rows)
    n_avg = n_tot / len(rows)
    g_wr  = sum(1 for r in rows if r["pnl"] > 0) / len(rows) * 100
    n_wr  = sum(1 for r in rows if r["net"] > 0) / len(rows) * 100
    print("\n【★ 扣費後總結算 ★】")
    print(f"  毛利總計   {g_tot:+10.1f}   (毛勝率 {g_wr:.1f}%)")
    print(f"  估計手續費 {-f_tot:10.1f}   (來回 {(OPEN_FEE_RATE+CLOSE_FEE_RATE)*100:.2f}% 名目；不含資金費)")
    print(f"  ─────────────────────")
    print(f"  淨利總計   {n_tot:+10.1f}   (淨勝率 {n_wr:.1f}%)")
    print(f"  淨期望值   {n_avg:+10.2f} / 筆")
    if len(rows) < 30:
        verdict = f"樣本僅 {len(rows)} 筆（<30）→ 一律視為雜訊，先別下結論"
    elif n_avg > 0:
        verdict = "扣費後期望值為正 → 初步看有 edge，但須樣本夠大且穩定才算數"
    else:
        verdict = "扣費後期望值為負 → 目前沒有 edge，別自我感覺良好"
    print(f"  ⚖ 判讀：{verdict}")

print("\n【A. 多空總覽】")
stat("全部", rows); stat("Long", longs); stat("Short", shorts)

print("\n【B. 做空 × 趨勢偏離 trend_gap】(看順勢空到底有沒有用)")
bucket("Short by trend_gap", shorts, "trend_gap",
       [-1e9, -10, -5, -3, 0, 1e9],
       ["強跌(<-10%)", "中跌(-10~-5)", "小跌(-5~-3)", "盤整(-3~0)", "上漲(>0)"])

print("\n【C. 做空 × RSI】")
bucket("Short by RSI", shorts, "rsi",
       [-1e9, 30, 40, 50, 1e9],
       ["RSI<30", "30-40", "40-50", "RSI>=50"])

print("\n【D. 做多 × RSI】(接刀深度)")
bucket("Long by RSI", longs, "rsi",
       [-1e9, 15, 20, 25, 30, 1e9],
       ["<15", "15-20", "20-25", "25-30", ">=30"])

print("\n【E. 做多 × 趨勢偏離】(順勢多 vs 逆勢接刀)")
bucket("Long by trend_gap", longs, "trend_gap",
       [-1e9, -5, -3, 0, 3, 1e9],
       ["強跌(<-5)", "小跌(-5~-3)", "微跌(-3~0)", "微漲(0~3)", "強漲(>3)"])

print("\n【F. 共振評分有沒有預測力】")
bucket("All by score", rows, "score",
       [-1e9, 60, 80, 1e9], ["<60", "60-79", "80+"])

print("\n【G. 爆量強度 vol_surge】")
bucket("All by vol_surge", rows, "vol_surge",
       [-1e9, 200, 300, 400, 1e9], ["<200", "200-300", "300-400", "400+"])

print("\n【H. 平倉原因】")
for reason in set(r["exit_reason"] for r in rows):
    stat(reason, [r for r in rows if r["exit_reason"] == reason])

print("\n（淨均/淨總/淨勝率＝扣手續費後；毛均＝原始價差。資金費未計入，故淨值略偏樂觀。）")
