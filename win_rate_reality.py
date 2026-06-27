"""
win_rate_reality.py — 「高勝率」的殘酷真相計算機

針對「勝率高、低賠率」這種偏好，算清楚：
  · 這個賠率下，光「不賠錢」需要多高勝率（損益平衡勝率）
  · 你的勝率扣費後，每筆期望值是正是負
  · 蒙地卡羅模擬：高勝率帳戶「看起來很順，然後一串連敗崩掉」的真相
    （最大回撤、破產機率）

★ 核心：勝率會騙人。真正決定生死的是「期望值」與「連敗時的尾部風險」。
  風控/概念練習用，非投資建議。改最上面參數試。
"""
import numpy as np

WIN_RATE = 0.85     # 你的勝率
PAYOFF   = 0.15     # 賠率 = 平均賺 / 平均賠（低賠率 = 贏得小）
FEE_R    = 0.02     # 每筆手續費(以「平均賠 = 1R」為單位)
N_TRADES = 200      # 一輪交易筆數
RISK_PER = 0.01     # 每筆冒帳戶 1%（平均賠 = 1% 帳戶）
SIMS     = 5000     # 模擬次數


def main():
    breakeven = 1 / (1 + PAYOFF)                       # 不含費的損益平衡勝率
    # 每筆期望值(R)：贏 +PAYOFF、輸 -1，再扣費
    exp_R = WIN_RATE * PAYOFF - (1 - WIN_RATE) * 1 - FEE_R
    print("=" * 60)
    print("  「高勝率 / 低賠率」殘酷真相")
    print("=" * 60)
    print(f"  你的勝率 / 賠率   : {WIN_RATE:.0%}  /  {PAYOFF:.2f} : 1")
    print(f"  損益平衡需要勝率  : {breakeven:.1%}   {'✅ 你過了' if WIN_RATE>breakeven else '🔴 你不到，注定虧'}（未計費）")
    print(f"  每筆手續費        : {FEE_R:.2f}R")
    print(f"  → 扣費後每筆期望  : {exp_R:+.3f}R   {'🟢 正期望' if exp_R>0 else '🔴 負期望（贏再多次也是慢性失血）'}")
    print("-" * 60)

    # 蒙地卡羅：模擬 SIMS 個帳戶各跑 N_TRADES 筆，看終值與最大回撤
    rng = np.random.default_rng(0)
    finals, maxdd, ruined = [], [], 0
    for _ in range(SIMS):
        eq = 1.0; peak = 1.0; dd = 0.0; dead = False
        for _ in range(N_TRADES):
            if rng.random() < WIN_RATE:
                eq *= 1 + RISK_PER * PAYOFF - RISK_PER * FEE_R
            else:
                eq *= 1 - RISK_PER * 1 - RISK_PER * FEE_R
            peak = max(peak, eq)
            dd = max(dd, 1 - eq / peak)
            if eq < 0.5:                                # 跌掉一半算重傷
                dead = True
        finals.append(eq); maxdd.append(dd)
        if dead: ruined += 1

    finals = np.array(finals)
    print(f"  模擬 {SIMS} 個帳戶各跑 {N_TRADES} 筆（每筆冒 {RISK_PER:.0%}）：")
    print(f"  終值中位數        : {np.median(finals):.2f}×（>1 賺、<1 賠）")
    print(f"  最差 5% 帳戶終值   : {np.percentile(finals,5):.2f}×")
    print(f"  平均最大回撤      : {np.mean(maxdd):.0%}")
    print(f"  曾腰斬(重傷)比例  : {ruined/SIMS:.0%}")
    print("=" * 60)
    print("  讀法：勝率 85% 聽起來無敵，但若低於損益平衡或扣費後負期望，")
    print("  你會『贏 8 成的牌局，輸掉整個帳戶』—— 因為輸的那 2 成賠得比贏的多。")
    print("  高勝率策略的命門永遠是『連敗的尾部』，不是平常那串小勝。")


if __name__ == "__main__":
    main()
