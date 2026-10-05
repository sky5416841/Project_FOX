# -*- coding: utf-8 -*-
"""H1 前瞻驗證(見 HYPOTHESES.md):PO3 大時框(30m/1h)毛利是否 > 0。
- 只使用 trade_id >= CUTOFF_MS 的新樣本(登記後才產生的資料)。
- 贏=+2R、輸=-1R(標籤實際驗證的目標,不得用CSV的tp/R:R計酬)。
- 去重疊:同 幣種×日期×時框 只取第一筆。
- 未滿 N_TARGET 筆:只印數量,【不印任何績效】(避免偷看)。
- 滿 N_TARGET 筆:取依時間序前 N_TARGET 筆,驗一次,寫入 H1_result.json 並鎖定,不再重算。
"""
import json, os, sys
import numpy as np
import pandas as pd
from scipy import stats

CUTOFF_MS = 1790942592183
N_TARGET = 300
T_THRESH = 2.0
CSV = os.path.join("ml_lab", "live_ml_features.csv")
RESULT = "H1_result.json"
TAKER_RT, MAKER_RT = 0.0010, 0.0004     # 來回手續費(吃單 0.05%x2 / 掛單 0.02%x2)


def main():
    if os.path.exists(RESULT):
        print("H1 已驗證並鎖定,結果如下(不再重算):")
        print(open(RESULT, encoding="utf-8").read())
        return
    d = pd.read_csv(CSV)
    d["trade_id"] = pd.to_numeric(d["trade_id"], errors="coerce")
    d = d[(d["trade_id"] >= CUTOFF_MS) & (d["tf"].isin(["30m", "1h"]))].copy()
    lab = pd.to_numeric(d["label"], errors="coerce")
    n_timeout = int((lab == -1).sum())
    n_pending = int(lab.isna().sum())
    d = d[lab.isin([0.0, 1.0])].copy()
    d["label"] = pd.to_numeric(d["label"])
    d["date"] = pd.to_datetime(d["datetime"], errors="coerce").dt.date
    d = d.sort_values("trade_id").drop_duplicates(subset=["symbol", "date", "tf"], keep="first")
    n = len(d)
    print("H1 前瞻驗證:登記後新樣本(已結算、去重疊) n=%d / 目標 %d  (逾時剔除 %d、未結算 %d)" % (
        n, N_TARGET, n_timeout, n_pending))
    if n < N_TARGET:
        print("尚未達標,依預先登記規則【不顯示任何績效】。")
        return

    d = d.head(N_TARGET)
    d["sd"] = (d["entry"] - d["sl"]).abs()
    d = d[d["sd"] > 1e-9]
    d["stop_pct"] = d["sd"] / d["entry"]
    gross = np.where(d["label"] == 1, 2.0, -1.0)
    t, p2 = stats.ttest_1samp(gross, 0)
    p1 = p2 / 2 if t > 0 else 1 - p2 / 2
    net_taker = gross - TAKER_RT / d["stop_pct"].values
    net_maker = gross - MAKER_RT / d["stop_pct"].values
    res = {
        "n": int(len(d)), "win_rate": float(d["label"].mean()),
        "gross_mean_R": float(gross.mean()), "gross_t": float(t), "one_sided_p": float(p1),
        "net_taker_mean_R": float(net_taker.mean()), "net_maker_mean_R": float(net_maker.mean()),
        "pass_primary": bool(gross.mean() > 0 and t > T_THRESH),
        "pass_economic": bool(gross.mean() > 0 and t > T_THRESH and net_maker.mean() > 0),
        "evaluated_at": pd.Timestamp.now().isoformat(),
    }
    json.dump(res, open(RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("=== H1 結果(單次,已鎖定) ===")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print("主要檢驗(毛利 t>%.1f): %s" % (T_THRESH, "✅通過" if res["pass_primary"] else "❌未通過 → 結案"))


if __name__ == "__main__":
    main()
