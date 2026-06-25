"""
data_pipeline.py — 逐筆成交 (Order Flow) 數據管線:Delta / CVD 計算基石

K 線(OHLCV)只告訴你「結果」,逐筆成交告訴你火拼的「真實力道」。
本模組把主觀盤感(「這筆動能不對」)量化成微觀市場結構指標:

  · Delta      = 主動買量 − 主動賣量(這段時間誰在真的追價)
  · CVD        = Delta 的累積(Cumulative Volume Delta,力道的累積趨勢)
  · 主動方判定 = CCXT 的 trade['side'] 即「吃單方(taker)」方向
                 (買者掛單成交=賣方主動→'sell';binance aggTrade 的 isBuyerMaker)

★ 誠實聲明:Delta/CVD 是貨真價實的機構級微觀結構工具,做出來是硬作品。
  但「加 Delta 過濾就能把負期望轉正、複製 VTuber」這個假設,與多時框 POI
  過濾屬同一類『context=edge』主張 —— 已被 QUANT_RESEARCH.md 的數據否定
  (純 ROP n=2041 t=-12.27;加結構過濾後不升反降)。本模組價值在『把主觀
  盤感量化』的工程能力,不是承諾賺錢。
"""
import time
import ccxt
import numpy as np
import pandas as pd


def make_exchange() -> ccxt.binance:
    return ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})


def fetch_trades(symbol: str = "BTC/USDT", limit: int = 1000, exchange=None) -> pd.DataFrame:
    """
    抓最近 N 筆逐筆成交(binance 合約 aggTrades,單次上限 1000)。
    回傳 DataFrame:ts, datetime, price, amount, side('buy'=主動買 / 'sell'=主動賣)。
    """
    ex = exchange or make_exchange()
    raw = ex.fetch_trades(symbol, limit=limit)
    rows = []
    for t in raw:
        side = t.get("side")
        if side is None:                       # 後備:用 takerOrMaker/info 推斷
            info = t.get("info", {})
            is_buyer_maker = info.get("m")     # aggTrade: True=買方掛單→賣方主動
            side = "sell" if is_buyer_maker else "buy"
        rows.append({"ts": t["timestamp"], "price": float(t["price"]),
                     "amount": float(t["amount"]), "side": side})
    df = pd.DataFrame(rows)
    if len(df):
        df["datetime"] = pd.to_datetime(df["ts"], unit="ms")
    return df


def calculate_delta_and_cvd(trades: pd.DataFrame) -> dict:
    """
    從逐筆成交算 Delta 力道差與 CVD。
    回傳 dict:buy_vol, sell_vol, delta, cvd_last, n,
             以及逐筆 CVD 序列(供畫圖/背離偵測用)。
    """
    if trades is None or len(trades) == 0:
        return {"n": 0, "buy_vol": 0.0, "sell_vol": 0.0, "delta": 0.0,
                "cvd_last": 0.0, "cvd": pd.Series(dtype=float)}

    buy_mask = trades["side"] == "buy"
    buy_vol = float(trades.loc[buy_mask, "amount"].sum())
    sell_vol = float(trades.loc[~buy_mask, "amount"].sum())
    delta = buy_vol - sell_vol

    # 逐筆有號量 → 累積得 CVD
    signed = np.where(buy_mask, trades["amount"], -trades["amount"])
    cvd = pd.Series(signed, index=trades.index).cumsum()

    return {"n": int(len(trades)), "buy_vol": buy_vol, "sell_vol": sell_vol,
            "delta": float(delta), "cvd_last": float(cvd.iloc[-1]), "cvd": cvd}


IMBALANCE_MIN = 0.15   # |Delta| 至少占總量這個比例才算「方向夠強」(否則視為中性、不確認)


def delta_breakout_filter(side: str, trades: pd.DataFrame) -> dict:
    """
    把 VTuber 的「這筆動能不對」直覺數學化:用 Delta 背離判掃針是真突破還假突破。

      · 上緣掃針→想做空(side='SHORT'):
          想看到 Delta 強烈『負』(價格被拉上去但實際在倒貨=假突破)→ 確認開空
          若 Delta 強烈『正』(散戶主力都真買=真突破)→ 放棄(這是 VTuber 閃掉的停損)
      · 下緣掃針→想做多(side='LONG'):對稱,想看到 Delta 強烈『正』。

    回傳 {passed, delta, ratio, reason}。
    ⚠ 僅適用 LIVE(逐筆只能抓近期),無法對歷史 K 棒回測 → 這是即時迴圈專用過濾器。
    """
    res = calculate_delta_and_cvd(trades)
    total = res["buy_vol"] + res["sell_vol"]
    delta = res["delta"]
    ratio = (delta / total) if total > 0 else 0.0   # 有號失衡比(-1~+1)

    if abs(ratio) < IMBALANCE_MIN:
        return {"passed": False, "delta": delta, "ratio": ratio,
                "reason": f"力道中性(|失衡|{abs(ratio):.0%}<{IMBALANCE_MIN:.0%}),不確認"}

    if side == "SHORT":
        passed = ratio < 0     # 想要賣方主導(背離)
        reason = "Delta 強烈為負=倒貨背離→確認假突破做空" if passed else \
                 "Delta 強烈為正=真買盤→真突破,放棄做空(閃停損)"
    else:  # LONG
        passed = ratio > 0
        reason = "Delta 強烈為正=真買盤背離→確認假突破做多" if passed else \
                 "Delta 強烈為負=真賣壓→真破底,放棄做多"
    return {"passed": passed, "delta": delta, "ratio": ratio, "reason": reason}


OBI_DEPTH_PCT = 0.005   # 計算掛單牆的價格深度範圍(中價上下各 0.5%)
OBI_WALL_MULT = 3.0     # 進場方向的「牆」需是對側的幾倍厚


def fetch_order_book(symbol: str = "BTC/USDT", limit: int = 100, exchange=None) -> dict:
    """抓當下掛單簿(Bids/Asks)。回傳 ccxt 標準 order book dict。"""
    ex = exchange or make_exchange()
    return ex.fetch_order_book(symbol, limit=limit)


def calculate_obi(order_book: dict, depth_pct: float = OBI_DEPTH_PCT) -> dict:
    """
    訂單簿失衡度:統計中價上下 depth_pct 內的買盤/賣盤總量。
    回傳 {mid, bid_vol(下方買牆), ask_vol(上方賣牆), ratio=ask/bid}。
    """
    bids, asks = order_book.get("bids", []), order_book.get("asks", [])
    if not bids or not asks:
        return {"mid": 0.0, "bid_vol": 0.0, "ask_vol": 0.0, "ratio": float("nan")}
    mid = (bids[0][0] + asks[0][0]) / 2
    lo, hi = mid * (1 - depth_pct), mid * (1 + depth_pct)
    bid_vol = sum(amt for px, amt in bids if px >= lo)      # 中價下方 0.5% 內的買單
    ask_vol = sum(amt for px, amt in asks if px <= hi)      # 中價上方 0.5% 內的賣單
    ratio = (ask_vol / bid_vol) if bid_vol > 0 else float("inf")
    return {"mid": mid, "bid_vol": float(bid_vol), "ask_vol": float(ask_vol), "ratio": float(ratio)}


def obi_filter(side: str, order_book: dict, wall_mult: float = OBI_WALL_MULT) -> dict:
    """
    訂單簿失衡牆過濾器:進場前確認主力已在『目標方向』佈好掛單陣地。
      · 做空(SHORT):上方賣牆 ≥ 下方買牆 × wall_mult → 主力壓著賣單,確認
      · 做多(LONG) :下方買牆 ≥ 上方賣牆 × wall_mult → 主力托著買單,確認
    回傳 {passed, ratio, bid_vol, ask_vol, reason}。
    """
    o = calculate_obi(order_book)
    bid_vol, ask_vol, ratio = o["bid_vol"], o["ask_vol"], o["ratio"]
    if side == "SHORT":
        passed = ask_vol >= wall_mult * bid_vol
        reason = (f"上方賣牆 {ask_vol:.1f} ≥ 下方買牆 {bid_vol:.1f}×{wall_mult:.0f}→確認賣壓陣地"
                  if passed else f"上方賣牆不足({ratio:.1f}×<{wall_mult:.0f}),無掩護,放棄做空")
    else:  # LONG
        passed = bid_vol >= wall_mult * ask_vol
        inv = (bid_vol / ask_vol) if ask_vol > 0 else float("inf")
        reason = (f"下方買牆 {bid_vol:.1f} ≥ 上方賣牆 {ask_vol:.1f}×{wall_mult:.0f}→確認買盤陣地"
                  if passed else f"下方買牆不足({inv:.1f}×<{wall_mult:.0f}),無支撐,放棄做多")
    return {"passed": passed, "ratio": ratio, "bid_vol": bid_vol, "ask_vol": ask_vol, "reason": reason}


def _test():
    symbol = "BTC/USDT"
    print(f"抓取 {symbol} 最近逐筆成交…")
    trades = fetch_trades(symbol, limit=1000)
    res = calculate_delta_and_cvd(trades)
    if res["n"] == 0:
        print("（無成交資料）")
        return
    span = (trades["datetime"].iloc[-1] - trades["datetime"].iloc[0])
    print(f"  筆數         : {res['n']}（時間跨度 ~{span}）")
    print(f"  主動買量     : {res['buy_vol']:.4f}")
    print(f"  主動賣量     : {res['sell_vol']:.4f}")
    print(f"  Delta(買-賣) : {res['delta']:+.4f}  → "
          f"{'買方主導(追價真買)' if res['delta'] > 0 else '賣方主導(倒貨/縮手)'}")
    print(f"  CVD 末值     : {res['cvd_last']:+.4f}")
    print("✓ Delta / CVD 計算管線就緒（下一步:接進 PO3 掃針當『真假突破』過濾器）")


if __name__ == "__main__":
    _test()
