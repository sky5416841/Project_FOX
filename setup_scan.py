# -*- coding: utf-8 -*-
"""Setup 機會掃描器(BTC/ETH)—— 用 4h 環境選 setup,盯進場區到了沒。

邏輯(照使用者定調:4h 是『選 setup 的方向盤』,不是開關):
  4h=range → Setup A 支撐反彈:現價貼近箱體下緣(支撐)才是機會,箱體中間/上緣不做。
  4h=up    → Setup C 趨勢回調:現價回踩到 EMA20 附近才是機會,追高不做。
  4h=down  → 只做多的話跳過(不接下跌刀)。
再要求 15m 收綠/兩燈當確認。符合就寫 _setup_flag.txt 提醒。**純警報不下單。**

用法:python setup_scan.py            掃一次
      python setup_scan.py --loop     每5分持續掃(看門狗接管)
"""
import warnings; warnings.filterwarnings("ignore")
import sys, os, time, datetime
import research.cv_predict as cp

SYMBOLS  = ["BTC/USDT", "ETH/USDT"]
NEAR_PCT = 0.004        # 現價距進場區 ≤0.4% 算「到了」
RR_MIN   = 1.5          # 賺賠比門檻(規則書 R4)
CONF_MIN = 0.75         # 4h信心值門檻(校準檢查發現:75%以下實際準確率會掉到55~65%甚至更低,
                        # 4h尤其在<60%時只有21%準,比瞎猜還差 → 低於此門檻視為「環境不明」不選任何Setup
LOG      = "_setup_scan.log"
FLAG     = "_setup_flag.txt"
INTERVAL = 300
MAX_RUNTIME = 6 * 3600  # 每跑滿6小時優雅退出→看門狗5分內重啟,釋放累積記憶體


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def scan_once():
    hits = []
    hits_data = []
    for sym in SYMBOLS:
        try:
            r4 = cp.classify(sym, "4h")
            r15 = cp.classify(sym, "15m")
        except Exception as e:
            log("ERR %s -> %s" % (sym, e)); continue
        cls = r4["cls"]
        # 信心值校準檢查(2026/9/23):4h信心<60%時實際準確率僅21%(比瞎猜還差),
        # 75~90%區間也只有76%、明顯打折。門檻以下=「環境不明」,不選任何Setup、直接跳過。
        if r4["conf"] < CONF_MIN:
            log("%s 4h=%s但信心僅%.0f%%(<%.0f%%門檻) → 環境不明,跳過不選Setup"
                % (sym.split("/")[0], cls, r4["conf"] * 100, CONF_MIN * 100))
            continue
        lv = r4.get("levels") or {}
        entry, sl, tp, rr, last = (lv.get("entry"), lv.get("sl"), lv.get("tp"),
                                   lv.get("rr", 0), lv.get("last"))
        if entry is None or last is None:
            continue
        if cls == "down":
            # 2026/9/30:曾短暫開放「SHORT+高R:R+僅BTC/ETH」實驗性做空(n=56,t=1.95),
            # 但隨即用嚴格標準重新檢驗(多重比較校正需t>2.89;去重疊樣本後t掉到1.38)都沒通過,
            # 判定是雜訊/多重比較假象,撤回。維持原規則:只做多,down直接跳過。
            log("%s 4h=down → 只做多故跳過" % sym.split("/")[0]); continue
        setup = "A支撐反彈" if cls == "range" else ("C趨勢回調" if cls == "up" else "?")
        # 現價是否已進到 setup 進場區(做多:現價 ≤ entry×(1+NEAR))
        near = last <= entry * (1 + NEAR_PCT)
        dist_pct = (last - entry) / entry * 100
        # 15m 確認:收綠 或 兩燈可交易
        l15 = r15.get("lights") or {}
        conf15 = (r15["cls"] in ("up",)) or l15.get("tradeable", False)
        ok = near and rr >= RR_MIN and conf15
        tag = "⭐機會!" if ok else ("接近(距%.2f%%)" % dist_pct if near else "等待(距進場區%.2f%%)" % dist_pct)
        log("%s 4h=%s→Setup%s | 現價%.0f 進場%.0f sl%.0f tp%.0f R:R%.2f | 15m=%s確認%s → %s"
            % (sym.split("/")[0], cls, setup, last, entry, sl, tp, rr, r15["cls"],
               "✓" if conf15 else "✗", tag))
        if ok:
            hits.append("%s Setup%s 進場區到了!現價%.0f 進場%.0f sl%.0f tp%.0f R:R%.2f"
                        % (sym.split("/")[0], setup, last, entry, sl, tp, rr))
            hits_data.append({"symbol": sym, "side": "long", "entry": entry, "sl": sl,
                              "tp": tp, "rr": rr})
    if hits:
        with open(FLAG, "w", encoding="utf-8") as f:
            f.write("Setup 機會 %s\n" % datetime.datetime.now().strftime("%m-%d %H:%M"))
            for h in hits:
                f.write(h + "\n")
        import json
        with open("_setup_flag.json", "w", encoding="utf-8") as f:
            json.dump(hits_data[0], f)   # 多筆機會時先給第一筆,給 live_order.py --from-flag 讀
    else:
        try:
            os.remove("_setup_flag.json")
        except FileNotFoundError:
            pass
        # 沒機會 → 清掉舊旗標,避免留著過期的機會被誤當現在還有效
        try:
            os.remove(FLAG)
        except FileNotFoundError:
            pass
    return hits


def main():
    loop = "--loop" in sys.argv
    if loop:
        log("=== Setup 掃描器啟動(每%ds掃 BTC/ETH,4h選setup盯進場區) ===" % INTERVAL)
        started = time.time()
        while True:
            try:
                if not scan_once():
                    pass
            except Exception as e:
                log("ERR loop %s" % e)
            if time.time() - started > MAX_RUNTIME:
                log("已跑滿 %.0fh,優雅退出讓看門狗重啟(釋放記憶體)" % (MAX_RUNTIME / 3600))
                break
            time.sleep(INTERVAL)
    else:
        hits = scan_once()
        print("\n⭐ 有 Setup 進場機會!已寫 %s" % FLAG if hits else "\n目前無 setup 進場機會(現價還沒到進場區,或無確認)。")


if __name__ == "__main__":
    main()
