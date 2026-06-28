"""
check_smc.py — 一鍵查看 SMC 自動交易器狀態 + ML 數據收集進度

用法(專案根目錄):  python check_smc.py
"""
import os
import json
import pandas as pd

STATE = "smc_paper_state.json"
CLOSED = "smc_paper_closed.csv"
ML = os.path.join("ml_lab", "smc_ml_features.csv")
TARGET = 500

print("=" * 50)
print("  SMC 自動交易器 — 帳戶狀態")
print("=" * 50)
if os.path.exists(STATE):
    s = json.load(open(STATE, encoding="utf-8"))
    eq = s.get("equity", 0)
    print(f"  虛擬權益    : ${eq:,.2f}  ({eq - 10000:+,.2f})")
    print(f"  當前持倉    : {len(s.get('open', []))}")
    for p in s.get("open", []):
        print(f"     ↳ {p['symbol']} {p['side']} 進場 {p['entry']:.4f} SL {p['sl']:.4f} TP {p['tp']:.4f}")
    print(f"  已平倉      : {s.get('closed_count', 0)} 筆")
    print(f"  累計淨損益  : {s.get('realized_pnl', 0):+,.2f}")
    print(f"  累計手續費  : -${s.get('fees_paid', 0):,.2f}")
else:
    print("  尚未開始(state 檔未生成)")

print()
print("=" * 50)
print("  ML 數據收集(7步全過的進場樣本)")
print("=" * 50)
if os.path.exists(ML):
    d = pd.read_csv(ML)
    lbl = pd.to_numeric(d["label"], errors="coerce")
    win = int((lbl == 1).sum()); loss = int((lbl == 0).sum())
    pend = int(lbl.isna().sum()); usable = win + loss
    print(f"  總收集筆數  : {len(d)}")
    print(f"  已結算      : {usable}  (Win {win} / Loss {loss}" + (f"，勝率 {win/usable:.0%})" if usable else ")"))
    print(f"  等待結算    : {pend}")
    print(f"  進度        : {usable} / {TARGET}  ({usable/TARGET:.1%})")
    if usable:
        print("\n  各市場分佈:")
        print(d.groupby("symbol").size().to_string())
else:
    print("  食盆還空的:還沒有任何『7步全過』的進場(很選擇性,正常)")
