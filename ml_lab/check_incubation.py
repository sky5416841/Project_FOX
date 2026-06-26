"""
check_incubation.py — 一鍵查看 ML 資料孵化進度

用法(在專案根目錄):
    python ml_lab/check_incubation.py
"""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "live_ml_features.csv")
TARGET = 500

if not os.path.exists(CSV):
    print("食盆還空的:尚未偵測到任何掃針(掃針稀有,正常)。")
    raise SystemExit

d = pd.read_csv(CSV)
lbl = pd.to_numeric(d["label"], errors="coerce")
win = int((lbl == 1).sum())
loss = int((lbl == 0).sum())
timeout = int((lbl == -1).sum())
pend = int(lbl.isna().sum())
usable = win + loss

print(f"總孵化筆數 : {len(d)}")
print(f"已打標可用 : {usable}  (Win {win} / Loss {loss}", end="")
print(f"，勝率 {win/usable:.0%})" if usable else ")")
print(f"等待結算   : {pend}")
print(f"逾時丟棄   : {timeout}")
print(f"進度       : {usable} / {TARGET}  ({usable/TARGET:.1%})")
if usable:
    print("\n各市場分佈:")
    print(d.groupby(["symbol", "tf"]).size().to_string())
print("\n（目標 ~500 筆可用樣本後，跑 ml_advanced_trainer_template.py 重訓含訂單流特徵的模型）")
