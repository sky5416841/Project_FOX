"""
交易紀律日誌 (Discipline Journal)
==================================
計分板是「行為」不是「損益」:每筆交易打「守了幾條規則」的分,而不是賺賠。
配合 DISCIPLINE_SYSTEM.md 使用。核心指標 = 連續守規矩的次數(streak),
因為「只要破一條就可能歸零」。

用法:
  python discipline_journal.py log      # 互動記一筆(逐條問有沒有守)
  python discipline_journal.py report   # 看紀律成長:紀律%、streak、罩門、行為vs損益
  python discipline_journal.py rules    # 列出規則書

記錄存 discipline_journal.csv(原子寫入)。損益(pnl)只記錄、不進紀律分。
"""
import argparse
import os
import sys
from datetime import datetime

import pandas as pd

CSV = "discipline_journal.csv"

# 規則書(可自行增減;id 要穩定,改字不影響歷史對照)
RULES = [
    ("R1", "進場前已定好停損價"),
    ("R2", "風險 ≤ 帳戶 1%"),
    ("R3", "賺賠比 ≥ 1.5"),
    ("R4", "是清單上的 setup(非衝動/FOMO)"),
    ("R5", "全程沒把停損往外移"),
    ("R6", "沒有對虧損單加碼"),
    ("R7", "沒超過當日虧損上限就收手"),
    ("R8", "沒有報復性/情緒性交易"),
]
RULE_IDS = [r[0] for r in RULES]


def _load():
    if os.path.exists(CSV):
        return pd.read_csv(CSV)
    return pd.DataFrame(columns=["datetime", "setup"] + RULE_IDS + ["score", "pnl", "note"])


def _save(df):
    tmp = CSV + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, CSV)


def _ask(prompt):
    """回傳 1(守) / 0(破) / None(不適用)。"""
    while True:
        a = input(prompt).strip().lower()
        if a in ("y", "yes", "1", ""):
            return 1
        if a in ("n", "no", "0"):
            return 0
        if a in ("na", "n/a", "-"):
            return None
        print("    請輸入 y(守) / n(破) / na(不適用)")


def log():
    df = _load()
    print("=" * 60)
    print("  記一筆交易 — 逐條問你有沒有守(y=守 / n=破 / na=不適用)")
    print("  ⚠ 誠實面對自己,這本日誌是給你看的,騙它等於騙自己")
    print("=" * 60)
    setup = input("這筆的 setup 名稱(例:回踩支撐做多): ").strip() or "(未命名)"
    marks, followed, applicable = {}, 0, 0
    for rid, desc in RULES:
        v = _ask(f"  [{rid}] {desc}? (y/n/na) ")
        marks[rid] = "" if v is None else v
        if v is not None:
            applicable += 1
            followed += v
    score = round(followed / applicable * 100) if applicable else 0
    pnl_raw = input("這筆損益 R 或 USDT(只記錄,不算分;可留空): ").strip()
    try:
        pnl = float(pnl_raw) if pnl_raw else ""
    except ValueError:
        pnl = ""
    note = input("備註(破了哪條、當下情緒…): ").strip()

    row = {"datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "setup": setup, **marks, "score": score, "pnl": pnl, "note": note}
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True) if not df.empty \
        else pd.DataFrame([row])
    _save(df)

    print("-" * 60)
    broken = [rid for rid in RULE_IDS if marks.get(rid) == 0]
    if score == 100:
        print(f"  ✅ 紀律分 100 — 乾淨的一筆!(不管賺賠,這筆你做對了)")
    else:
        print(f"  紀律分 {score} — 破了:{', '.join(broken)}  ← 這才是這筆真正的問題,不是損益")
    print(f"  已記錄第 {len(df)} 筆。用 report 看你的成長。")
    print("=" * 60)


def _clean_streak(scores):
    """從最後往前數,連續 100 分的筆數。"""
    s = 0
    for v in reversed(list(scores)):
        if v == 100:
            s += 1
        else:
            break
    return s


def report():
    df = _load()
    n = len(df)
    if n == 0:
        print("還沒有紀錄。先 `python discipline_journal.py log` 記一筆。")
        return
    disc = df["score"].mean()
    streak = _clean_streak(df["score"])
    clean = int((df["score"] == 100).sum())

    print("=" * 64)
    print("  紀律成長報告 — 計分板是行為,不是損益")
    print("=" * 64)
    print(f"  總筆數 {n}   平均紀律分 {disc:.0f}   乾淨筆數(100分) {clean}/{n} = {clean/n*100:.0f}%")
    print(f"  🔥 目前連續守規矩 streak = {streak} 筆   (階段1畢業標準:連續 20 筆)")
    if streak >= 20:
        print("     → 你已達到「連續20筆乾淨」,紀律肌肉建立起來了,可進下一階!")
    print("-" * 64)

    # 罩門:每條規則的破戒次數
    print("  你的破戒分布(數字大=你的罩門,專練這條):")
    stats = []
    for rid, desc in RULES:
        col = pd.to_numeric(df[rid], errors="coerce")
        broke = int((col == 0).sum())
        appl = int(col.notna().sum())
        stats.append((rid, desc, broke, appl))
    for rid, desc, broke, appl in sorted(stats, key=lambda x: -x[2]):
        bar = "█" * broke
        flag = "  ← 最大罩門" if broke == max(s[2] for s in stats) and broke > 0 else ""
        print(f"    [{rid}] 破 {broke:>2} 次 / 共 {appl:>2} 筆  {bar}{flag}")
        print(f"         {desc}")

    # 行為 vs 損益:守規矩到底有沒有「感覺上」比較好(教學:過程 > 單筆結果)
    pnl = pd.to_numeric(df["pnl"], errors="coerce")
    have = pnl.notna()
    if have.sum() >= 4:
        cleanmask = (df["score"] == 100) & have
        dirtymask = (df["score"] < 100) & have
        print("-" * 64)
        print("  守規矩 vs 破戒 的平均損益(有填損益的筆):")
        if cleanmask.sum():
            print(f"    ✅ 守規矩({cleanmask.sum()}筆) 平均 {pnl[cleanmask].mean():+.2f}")
        if dirtymask.sum():
            print(f"    ❌ 破戒  ({dirtymask.sum()}筆) 平均 {pnl[dirtymask].mean():+.2f}")
        print("  註:守規矩不保證單筆賺(市場有隨機性),但它保護你不被一次破戒歸零。")
    print("=" * 64)


def rules():
    print("=" * 60)
    print("  交易規則書(詳見 DISCIPLINE_SYSTEM.md)")
    print("=" * 60)
    for rid, desc in RULES:
        print(f"  [{rid}] {desc}")
    print("=" * 60)
    print("  紀律分 = 守了幾條 / 適用幾條 × 100。核心指標=連續100分的 streak。")


def main():
    ap = argparse.ArgumentParser(description="交易紀律日誌(計分行為,不計損益)")
    ap.add_argument("mode", nargs="?", default="report", choices=["log", "report", "rules"])
    args = ap.parse_args()
    if args.mode == "log":
        try:
            log()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消,未記錄。")
            sys.exit(0)
    elif args.mode == "rules":
        rules()
    else:
        report()


if __name__ == "__main__":
    main()
