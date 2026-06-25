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
