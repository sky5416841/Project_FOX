"""
PO3 第二課:操縱 / 假突破 (Manipulation / Liquidity Grab) 偵測器

承接第一課的盤整框(藍框 = 散戶停損池)。第二課把「主力誘多誘空」的盤感
翻成數學定義:

  盤整框成形後,價格常會先「衝出框外、掃掉框外的停損(流動性掠奪)」,
  再立刻被打回框內 —— 這根 K 棒就是「操縱」的足跡:

    · 上緣掃針(誘多): high 刺破 box_high,但 close 收回框內
        → 掃掉空單停損 / 套牢追高的多單 → 預期向下 markup(看跌)
    · 下緣掃針(誘空): low 刺破 box_low,但 close 收回框內
        → 掃掉多單停損 / 套牢殺低的空單 → 預期向上 markup(看漲)

  ROP(Rejection Of Price)確認:那根掃針反向影線要夠長(SWEEP_WICK_MIN × ATR),
  代表價格被「狠狠拒絕」—— 這就是真機 1m 上看到的 rejection 雛形。

★ 純技能/視覺練習:目標是「框 + 掃針標得對不對」,不是賺錢
  (這套 edge 已被嚴格回測證實扣費後不顯著,見 QUANT_RESEARCH.md)。
  本檔只做偵測 + 畫圖讓人監工;回測在下一步。
"""
import ccxt
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

SYMBOL, TF, LIMIT = "BTC/USDT", "5m", 500
WINDOW, ATR_LEN = 30, 14
RANGE_MULT = 0.95          # 震幅 < 近期典型震幅 × 此值 視為盤整收斂
MIN_BARS = 8              # 盤整至少持續幾根才算數

# --- 操縱偵測參數 ---
HUNT_BARS = 16           # 框結束後,往後幾根內找掃針
PIERCE_MIN = 0.0004      # 影線至少要刺破框緣多少比例(0.04%)才算真衝出
SWEEP_WICK_MIN = 0.4     # 反向(刺破方向)影線長度 ≥ 此倍 × ATR 才算「被狠狠拒絕(ROP)」

# --- 第三課:訊號後追蹤(Forward Return / MFE-MAE)參數 ---
SL_BUFFER = 0.0003       # 停損放在掃針極值外的微小緩衝(0.03%)
MAX_HOLD = 60            # 最多往前追蹤幾根 K 線(逾時 = 沒結果,以收盤平倉)


CACHE = "assets/po3_data.csv"   # 凍結資料:天選之針要可重現,就不能每次抓即時最新K線


def fetch():
    import os
    # 已凍結 → 固定讀同一份(K#113 才追蹤得到);要換新資料就刪掉 po3_data.csv
    if os.path.exists(CACHE):
        print(f"(使用已凍結資料 {CACHE};要重抓即時資料請刪除此檔)")
        return pd.read_csv(CACHE)
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    raw = ex.fetch_ohlcv(SYMBOL, TF, limit=LIMIT)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])
    df.to_csv(CACHE, index=False)
    print(f"(已凍結 {len(df)} 根資料 → {CACHE})")
    return df


def atr(df, n):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def find_boxes(df):
    """第一課邏輯:把連續的盤整 K 棒合併成方塊 (i0, i1, box_high, box_low)。"""
    df["rmax"] = df["high"].rolling(WINDOW).max()
    df["rmin"] = df["low"].rolling(WINDOW).min()
    df["rng"]  = (df["rmax"] - df["rmin"]) / df["rmin"]
    rng_med = df["rng"].rolling(200, min_periods=50).median()
    a = (df["rng"] < rng_med * RANGE_MULT).fillna(False).values

    boxes, i = [], 0
    while i < len(df):
        if a[i]:
            j = i
            while j < len(df) and a[j]:
                j += 1
            if j - i >= MIN_BARS:
                seg = df.iloc[i:j]
                boxes.append((i, j - 1, float(seg["high"].max()), float(seg["low"].min())))
            i = j
        else:
            i += 1
    return boxes


def find_manipulations(df, boxes):
    """
    每個框成形後 HUNT_BARS 根內,找第一根「掃針 K 棒」:
      影線刺破框緣 + 收盤拉回框內 + 反向影線夠長(ROP)。
    回傳 list of dict,含方向、發生位置、影線資訊。
    """
    atr_arr = df["atr"].values
    events = []
    for (i0, i1, bh, bl) in boxes:
        lo = i1 + 1
        hi = min(i1 + HUNT_BARS, len(df) - 1)
        for k in range(lo, hi + 1):
            o, h, l, c = (df["open"].iat[k], df["high"].iat[k],
                          df["low"].iat[k], df["close"].iat[k])
            cur_atr = atr_arr[k]
            if not np.isfinite(cur_atr) or cur_atr <= 0:
                continue

            # 上緣誘多掃針:刺破 box_high、收回框內、上影線夠長
            pierced_up = h > bh * (1 + PIERCE_MIN) and c < bh
            up_wick = h - max(o, c)
            if pierced_up and up_wick >= SWEEP_WICK_MIN * cur_atr:
                events.append(dict(box=(i0, i1, bh, bl), k=k, side="bear",
                                   level=bh, wick=up_wick, price_h=h, price_l=l,
                                   atr=cur_atr))
                break

            # 下緣誘空掃針:刺破 box_low、收回框內、下影線夠長
            pierced_dn = l < bl * (1 - PIERCE_MIN) and c > bl
            dn_wick = min(o, c) - l
            if pierced_dn and dn_wick >= SWEEP_WICK_MIN * cur_atr:
                events.append(dict(box=(i0, i1, bh, bl), k=k, side="bull",
                                   level=bl, wick=dn_wick, price_h=h, price_l=l,
                                   atr=cur_atr))
                break
    return events


def track_forward(df, e):
    """
    PO3 最終驗證:把單一操縱訊號放上解剖台,做「訊號後前向追蹤」。

    虛擬交易模型:
      · 進場:掃針 K 棒(k)的「下一根開盤價」(k+1 open) —— 不偷看未來
      · 看跌操縱(誘多掃針) → 做空
          SL = 掃針最高價 × (1+SL_BUFFER)   (掃完上緣若繼續漲就認錯)
          TP = box_low                       (主力理論上接著去掃下緣流動性)
      · 看漲操縱(誘空掃針) → 做多(對稱)
          SL = 掃針最低價 × (1-SL_BUFFER)
          TP = box_high

    逐根 look-forward 到先碰 SL 或 TP;同根都碰 → 保守假設先觸 SL。
    回傳含結果、實現 R、MFE/MAE(以 R 為單位)的 dict。
    """
    i0, i1, bh, bl = e["box"]
    k = e["k"]
    if k + 1 >= len(df):
        return None
    entry = float(df["open"].iat[k + 1])

    if e["side"] == "bear":          # 做空
        sl = e["price_h"] * (1 + SL_BUFFER)
        tp = bl
        risk = sl - entry            # 每 1R 的價格距離
        direction = -1
    else:                            # 做多
        sl = e["price_l"] * (1 - SL_BUFFER)
        tp = bh
        risk = entry - sl
        direction = +1

    if risk <= 0:                    # 退化情形(進場已穿 SL),跳過
        return None
    reward_dist = abs(tp - entry)
    rr_target = reward_dist / risk   # 理論盈虧比

    outcome, exit_price, exit_k = "OPEN", None, None
    mfe = mae = 0.0                  # 最大有利 / 不利(價格,之後換算成 R)
    end = min(k + 1 + MAX_HOLD, len(df))
    for j in range(k + 1, end):
        h, l = float(df["high"].iat[j]), float(df["low"].iat[j])
        # 追蹤過程中的浮動極值(以進場價為基準、依方向)
        fav = (h - entry) if direction > 0 else (entry - l)
        adv = (entry - l) if direction > 0 else (h - entry)
        mfe = max(mfe, fav); mae = max(mae, adv)

        if direction < 0:            # 做空:漲到 SL=輸,跌到 TP=贏
            hit_sl = h >= sl
            hit_tp = l <= tp
        else:                        # 做多:跌到 SL=輸,漲到 TP=贏
            hit_sl = l <= sl
            hit_tp = h >= tp

        if hit_sl and hit_tp:        # 同根雙觸 → 保守當輸
            outcome, exit_price, exit_k = "LOSS", sl, j; break
        if hit_sl:
            outcome, exit_price, exit_k = "LOSS", sl, j; break
        if hit_tp:
            outcome, exit_price, exit_k = "WIN", tp, j; break

    if outcome == "OPEN":            # 逾時未結 → 以最後收盤平倉
        exit_k = end - 1
        exit_price = float(df["close"].iat[exit_k])

    realized_r = direction * (exit_price - entry) / risk
    return dict(entry=entry, sl=sl, tp=tp, entry_k=k + 1, exit_k=exit_k,
                exit_price=exit_price, outcome=outcome, realized_r=realized_r,
                rr_target=rr_target, mfe_r=mfe / risk, mae_r=mae / risk,
                side=e["side"])


def main():
    df = fetch()
    df["atr"] = atr(df, ATR_LEN)
    boxes = find_boxes(df)
    events = find_manipulations(df, boxes)
    for e in events:
        e["track"] = track_forward(df, e)

    print(f"{SYMBOL} {TF} {len(df)}根 → 盤整框 {len(boxes)} 個,操縱/假突破訊號 {len(events)} 個")
    print("=" * 64)
    rs = []
    for e in events:
        tag = "誘多→看跌(空)" if e["side"] == "bear" else "誘空→看漲(多)"
        t = e["track"]
        print(f"  · K#{e['k']:>3}  {tag}  掃 {e['level']:.1f}  影線={e['wick']:.1f}({e['wick']/e['atr']:.1f}×ATR)")
        if t is None:
            print("      └ 無法追蹤(訊號在資料尾端或進場即穿停損)")
            continue
        icon = {"WIN": "✓ 打到停利", "LOSS": "✗ 反轉停損", "OPEN": "… 逾時平倉"}[t["outcome"]]
        print(f"      ├ 進場@{t['entry']:.1f}(K#{t['entry_k']})  SL={t['sl']:.1f}  TP={t['tp']:.1f}  目標 {t['rr_target']:.2f}R")
        print(f"      ├ 結果:{icon}  出場@{t['exit_price']:.1f}(K#{t['exit_k']})  實現 {t['realized_r']:+.2f}R")
        print(f"      └ MFE(最大有利)={t['mfe_r']:.2f}R   MAE(最大不利)={t['mae_r']:.2f}R")
        rs.append(t["realized_r"])
    if rs:
        arr = np.array(rs)
        wins = int((arr > 0).sum())
        print("-" * 64)
        print(f"  小樣本彙總(n={len(rs)}):勝 {wins}/{len(rs)}  "
              f"總計 {arr.sum():+.2f}R  平均 {arr.mean():+.2f}R/筆")
        print("  ⚠ n 過小,無統計意義 —— 這是『單筆解剖』而非可信回測(別過度解讀)")

    fig, ax = plt.subplots(figsize=(16, 8))
    for i, r in df.iterrows():
        col = "#26a69a" if r["close"] >= r["open"] else "#ef5350"
        ax.plot([i, i], [r["low"], r["high"]], color=col, lw=0.6)
        ax.plot([i, i], [r["open"], r["close"]], color=col, lw=2.0)
    for x0, x1, bh, bl in boxes:
        ax.add_patch(Rectangle((x0, bl), x1 - x0, bh - bl,
                               facecolor="#42a5f5", alpha=0.15, edgecolor="#1565c0", lw=1.0))
        ax.hlines([bh, bl], x0, x1, color="#1565c0", lw=0.7, ls=":")

    for e in events:
        k, side, lvl, wick = e["k"], e["side"], e["level"], e["wick"]
        if side == "bear":   # 上緣掃針 → 向下箭頭標在高點
            ax.annotate("", xy=(k, e["price_h"]), xytext=(k, e["price_h"] + wick * 1.2),
                        arrowprops=dict(arrowstyle="-|>", color="#d32f2f", lw=1.6))
            ax.scatter([k], [e["price_h"]], marker="v", s=70, color="#d32f2f", zorder=5)
        else:                # 下緣掃針 → 向上箭頭標在低點
            ax.annotate("", xy=(k, e["price_l"]), xytext=(k, e["price_l"] - wick * 1.2),
                        arrowprops=dict(arrowstyle="-|>", color="#2e7d32", lw=1.6))
            ax.scatter([k], [e["price_l"]], marker="^", s=70, color="#2e7d32", zorder=5)

        # 第三課:畫出「擴張」的真實路徑 —— 進場 → 出場(綠=打到TP / 紅=打到SL)
        t = e.get("track")
        if t is None:
            continue
        path_col = "#2e7d32" if t["outcome"] == "WIN" else ("#d32f2f" if t["outcome"] == "LOSS" else "#9e9e9e")
        ax.plot([t["entry_k"], t["exit_k"]], [t["entry"], t["exit_price"]],
                color=path_col, lw=1.6, ls="--", zorder=6)
        ax.scatter([t["entry_k"]], [t["entry"]], marker="o", s=45,
                   facecolor="white", edgecolor="#212121", lw=1.2, zorder=7)
        ax.scatter([t["exit_k"]], [t["exit_price"]], marker="X", s=80,
                   color=path_col, zorder=7)
        ax.annotate(f"{t['realized_r']:+.2f}R", xy=(t["exit_k"], t["exit_price"]),
                    xytext=(6, 0), textcoords="offset points", va="center",
                    fontsize=9, color=path_col, fontweight="bold")
        # TP / SL 水平參考線
        ax.hlines(t["tp"], t["entry_k"], t["exit_k"], color="#2e7d32", lw=0.7, ls=(0, (1, 2)), alpha=0.6)
        ax.hlines(t["sl"], t["entry_k"], t["exit_k"], color="#d32f2f", lw=0.7, ls=(0, (1, 2)), alpha=0.6)

    ax.add_patch(Rectangle((0, 0), 0, 0, facecolor="#42a5f5", alpha=0.4, label="盤整區(停損池)"))
    ax.scatter([], [], marker="v", color="#d32f2f", label="誘多掃針 → 看跌操縱")
    ax.scatter([], [], marker="^", color="#2e7d32", label="誘空掃針 → 看漲操縱")
    ax.plot([], [], color="#2e7d32", ls="--", label="擴張路徑→打到停利(Win)")
    ax.plot([], [], color="#d32f2f", ls="--", label="擴張路徑→反轉停損(Loss)")
    ax.set_title(f"{SYMBOL} {TF} — PO3 第二課:操縱/假突破偵測(掃針後收回框內 = 流動性掠奪)", fontsize=13)
    ax.set_xlabel("K 線序號"); ax.set_ylabel("價格"); ax.legend(loc="best", fontsize=9)
    ax.grid(alpha=0.12)
    fig.tight_layout(); fig.savefig("assets/po3_manipulation.png", dpi=110)
    print("✓ 已輸出 assets/po3_manipulation.png")


if __name__ == "__main__":
    main()
