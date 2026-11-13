# -*- coding: utf-8 -*-
"""forward_test_h9.py — H9 計數器與「訊號單 vs 同週隨機進場對照組」檢定。規則見 HYPOTHESES.md 的 H9。
未滿 40 筆只印筆數與成交率，不印任何績效；滿 40 筆且對照組都可結算後驗一次，寫 H9_result.json 並鎖定。"""
import os, sys, json, math, random, time, datetime, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE)
import radar_autotrade as at

N_TARGET, T_THRESH, RES = 40, 2.0, "H9_result.json"
N_CTRL, MIN_CTRL = 20, 5
BAR = at.BAR_MS
WAIT_MS = 74 * 3600 * 1000          # 週結束後再等 74 小時，確保對照單都已結算（8 根掛單 + 288 根持倉）


def week_start_ms(ms):
    d = datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc)
    d0 = (d - datetime.timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d0.timestamp() * 1000)


def fetch_range(sym, start_ms, end_ms):
    out, cur = [], start_ms
    while cur < end_ms:
        url = "https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&startTime=%d&limit=1000" % (sym.replace("/", ""), cur)
        req = urllib.request.Request(url, headers={"User-Agent": "FOX-radar/1.0"})
        k = json.loads(urllib.request.urlopen(req, timeout=20).read())
        if not k:
            break
        out += [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in k]
        cur = int(k[-1][0]) + BAR
        if len(k) < 1000:
            break
    return [b for b in out if b[0] + BAR <= int(time.time() * 1000)]


def main():
    if os.path.exists(RES):
        print("H9 已驗過並鎖定：", open(RES, encoding="utf-8").read()); return
    tr = at.load()["trades"]
    closed = sorted([t for t in tr if t["status"] == "CLOSED"], key=lambda t: t["fill_ms"])
    canc = [t for t in tr if t["status"] == "CANCELLED"]
    act = [t for t in tr if t["status"] in ("PENDING", "OPEN")]
    filled = len(closed) + len([t for t in act if t["status"] == "OPEN"])
    den = len(closed) + len(canc) + len(act)
    print("H9 前瞻樣本：已成交且結束 %d / %d 筆（進行中 %d、未成交取消 %d；成交率 %s）"
          % (len(closed), N_TARGET, len(act), len(canc), "%.0f%%" % (100.0 * filled / den) if den else "—"))
    if len(closed) < N_TARGET:
        return
    samp = closed[:N_TARGET]
    now = int(time.time() * 1000)
    ready_ms = max(week_start_ms(t["sig_open_ms"]) + 7 * 86400 * 1000 + WAIT_MS for t in samp)
    if now < ready_ms:
        print("對照組等待中：最晚一筆要到 %s（台灣時間）才能結算，尚未驗、未鎖定。"
              % datetime.datetime.fromtimestamp(ready_ms / 1000).strftime("%m/%d %H:%M")); return
    cache, ds, sig_r, ctrl_r, dropped = {}, [], [], [], 0
    for t in samp:
        ws = week_start_ms(t["sig_open_ms"]); we = ws + 7 * 86400 * 1000
        key = (t["symbol"], ws)
        if key not in cache:
            cache[key] = fetch_range(t["symbol"], ws, we + WAIT_MS)
        bars = cache[key]
        idx = [i for i, b in enumerate(bars) if ws <= b[0] < we and not (t["sig_open_ms"] <= b[0] <= (t["exit_ms"] or 0))]
        if not t.get("sig_close") or len(idx) < N_CTRL:
            dropped += 1; continue
        rng = random.Random(t["id"])
        picks = rng.sample(idx, N_CTRL)
        rs_ = []
        for i in picks:
            p = bars[i][4]
            e_c = p * (t["entry"] / t["sig_close"])
            sl_c = e_c * (t["sl"] / t["entry"]); tp_c = e_c * (t["tp"] / t["entry"])
            status, net = at.sim_trade(bars[i + 1:], t["side"], e_c, sl_c, tp_c)
            if status == "CLOSED":
                rs_.append(net)
        if len(rs_) < MIN_CTRL:
            dropped += 1; continue
        c_mean = sum(rs_) / len(rs_)
        ds.append(t["net_R"] - c_mean); sig_r.append(t["net_R"]); ctrl_r.append(c_mean)
    n = len(ds)
    if n < 2:
        print("可用樣本不足（剔除 %d）：無法判定。" % dropped); return
    m = sum(ds) / n; sd = math.sqrt(sum((x - m) ** 2 for x in ds) / (n - 1))
    t_stat = m / (sd / math.sqrt(n)) if sd > 0 else 0.0
    out = dict(n=n, dropped=dropped, mean_diff_R=round(m, 4), t=round(t_stat, 3),
               mean_signal_R=round(sum(sig_r) / n, 4), mean_control_R=round(sum(ctrl_r) / n, 4),
               passed=bool(t_stat > T_THRESH))
    json.dump(out, open(RES, "w", encoding="utf-8"), ensure_ascii=False)
    print("★ 已驗一次並鎖定：", out)


if __name__ == "__main__":
    main()
