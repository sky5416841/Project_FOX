"""
plot_incubation.py — 把 ML 孵化資料畫成圖(進度 + 各市場勝負 + 特徵預覽)

用法(專案根目錄):  python ml_lab/plot_incubation.py
輸出:  assets/incubation_status.png
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "live_ml_features.csv")
OUT = os.path.join(os.path.dirname(HERE), "assets", "incubation_status.png")
TARGET = 500

d = pd.read_csv(CSV)
d["lbl"] = pd.to_numeric(d["label"], errors="coerce")
done = d[d["lbl"].isin([0, 1])].copy()
usable = len(done)
win = int((done["lbl"] == 1).sum())

fig = plt.figure(figsize=(15, 5))
fig.suptitle(f"PO3 ML 資料孵化現況　已打標 {usable} / 目標 {TARGET}（n 仍小，僅預覽）", fontsize=14)

# (1) 進度條
ax1 = fig.add_subplot(1, 3, 1)
ax1.barh([0], [TARGET], color="#e0e0e0")
ax1.barh([0], [usable], color="#42a5f5")
ax1.set_xlim(0, TARGET); ax1.set_ylim(-1, 1); ax1.set_yticks([])
ax1.set_title(f"孵化進度 {usable/TARGET:.1%}")
ax1.text(TARGET/2, 0, f"{usable} / {TARGET}", ha="center", va="center", fontsize=12, fontweight="bold")
ax1.set_xlabel("可用樣本數")

# (2) 各市場 Win/Loss 堆疊
ax2 = fig.add_subplot(1, 3, 2)
if usable:
    g = done.groupby("symbol")["lbl"].agg(["sum", "count"])
    g["loss"] = g["count"] - g["sum"]
    g = g.sort_values("count")
    ax2.barh(g.index, g["sum"], color="#26a69a", label=f"Win ({win})")
    ax2.barh(g.index, g["loss"], left=g["sum"], color="#ef5350", label=f"Loss ({usable-win})")
    ax2.legend(fontsize=9)
ax2.set_title(f"各市場勝負（總勝率 {win/usable:.0%}）" if usable else "各市場勝負")
ax2.set_xlabel("樣本數")

# (3) 特徵預覽:Delta vs 影線/ATR,顏色=勝負(預覽未來 ML 看的東西)
ax3 = fig.add_subplot(1, 3, 3)
if usable and "delta" in done:
    colors = np.where(done["lbl"] == 1, "#26a69a", "#ef5350")
    ax3.scatter(done["delta"], done["wick_atr"], c=colors, s=80, edgecolor="#333", lw=0.6)
    ax3.axvline(0, color="#999", lw=0.8, ls=":")
ax3.set_title("特徵預覽：Delta × 影線/ATR")
ax3.set_xlabel("taker Delta（主動買-賣）"); ax3.set_ylabel("wick_atr（影線/ATR）")
ax3.scatter([], [], c="#26a69a", label="Win"); ax3.scatter([], [], c="#ef5350", label="Loss")
ax3.legend(fontsize=9); ax3.grid(alpha=0.15)

fig.tight_layout(rect=[0, 0, 1, 0.94])
fig.savefig(OUT, dpi=110); plt.close(fig)
print(f"✓ 已輸出 {OUT}（{usable} 筆已打標，Win {win} / Loss {usable-win}）")
