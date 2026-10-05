# -*- coding: utf-8 -*-
"""盯手動模擬倉:掛單 #7 一成交(open 出現)就印出詳情並結束,讓上層通知使用者。"""
import warnings; warnings.filterwarnings("ignore")
import time, json, sys

STATE = "paper_manual_state.json"
MAX_HOURS = 24

def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

deadline = time.time() + MAX_HOURS * 3600
start_open = len(load().get("open", [])) if load() else 0
while time.time() < deadline:
    s = load()
    if s and len(s.get("open", [])) > start_open:
        p = s["open"][-1]
        print("FILLED #%s %s %s @%g sl%g tp%g" % (
            p["id"], p["symbol"], p["side"], p["entry"], p["sl"], p["tp"]), flush=True)
        sys.exit(0)
    # 掛單消失但沒新持倉 = 可能被撤或異常,也結束回報
    if s and len(s.get("pending", [])) == 0 and len(s.get("open", [])) == start_open:
        print("PENDING_GONE 掛單不見了(可能被撤/異常),沒有新持倉", flush=True)
        sys.exit(0)
    time.sleep(30)
print("TIMEOUT 盯了 %dh 還沒成交" % MAX_HOURS, flush=True)
