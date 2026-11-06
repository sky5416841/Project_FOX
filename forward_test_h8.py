# -*- coding: utf-8 -*-
"""forward_test_h8.py — H8 計數器。未滿 40 筆只印筆數與成交率，不印任何績效；滿 40 筆驗一次後鎖定。"""
import os, sys, json, math
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE)
N_TARGET, T_THRESH, RES = 40, 2.0, "H8_result.json"
if os.path.exists(RES):
    print("H8 已驗過並鎖定：", open(RES, encoding="utf-8").read()); sys.exit(0)
try:
    tr = json.load(open("autotrade_state.json", encoding="utf-8"))["trades"]
except Exception:
    tr = []
closed = sorted([t for t in tr if t["status"] == "CLOSED"], key=lambda t: t["fill_ms"])
canc = [t for t in tr if t["status"] == "CANCELLED"]
act = [t for t in tr if t["status"] in ("PENDING", "OPEN")]
filled = len(closed) + len([t for t in act if t["status"] == "OPEN"])
den = len(closed) + len(canc) + len(act)
rate = "%.0f%%" % (100.0 * filled / den) if den else "—"
print("H8 前瞻樣本：已成交且結束 %d / %d 筆（進行中 %d、未成交取消 %d；成交率 %s）"
      % (len(closed), N_TARGET, len(act), len(canc), rate))
if len(closed) >= N_TARGET:
    r = [t["net_R"] for t in closed[:N_TARGET]]
    n = len(r); m = sum(r) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / (n - 1))
    t = m / (sd / math.sqrt(n)) if sd > 0 else 0.0
    out = dict(n=n, mean_net_R=round(m, 4), t=round(t, 3), win_rate=round(sum(x > 0 for x in r) / n, 3), passed=bool(t > T_THRESH))
    json.dump(out, open(RES, "w", encoding="utf-8"), ensure_ascii=False)
    print("★ 已驗一次並鎖定：", out)
