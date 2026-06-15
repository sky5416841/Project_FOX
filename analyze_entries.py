"""進場條件分析器：找出「哪種條件真的有正期望值」。"""
import sqlite3

c = sqlite3.connect("fox_trading.db")
c.row_factory = sqlite3.Row
# 只取有 context 的交易（rsi 或 trend_gap 非 0）
rows = [dict(r) for r in c.execute(
    "SELECT * FROM trade_history WHERE (rsi!=0 OR trend_gap!=0)").fetchall()]


def stat(label, subset):
    n = len(subset)
    if n == 0:
        print(f"{label:<22} 筆數=0")
        return
    wins = sum(1 for r in subset if r["pnl"] > 0)
    wr = wins / n * 100
    avg = sum(r["pnl"] for r in subset) / n
    tot = sum(r["pnl"] for r in subset)
    print(f"{label:<22} 筆數={n:<4} 勝率={wr:5.1f}%  平均={avg:+8.1f}  總計={tot:+9.1f}")


def bucket(name, subset, key, edges, labels):
    print(f"\n── {name} ──")
    for i, lab in enumerate(labels):
        lo = edges[i]; hi = edges[i + 1]
        sub = [r for r in subset if lo <= r[key] < hi]
        stat(lab, sub)


longs  = [r for r in rows if r["side"] == "Long"]
shorts = [r for r in rows if r["side"] == "Short"]

print("="*70)
print(f"總樣本（有context）：{len(rows)} 筆")
print("="*70)
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
