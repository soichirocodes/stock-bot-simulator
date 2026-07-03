"""Rule-based trading signals: SMA crossover + RSI.

Signal logic (evaluated on daily bars):
- BUY  when SMA5 crosses above SMA20 (golden cross), or RSI(14) < 30 (oversold)
- SELL when SMA5 crosses below SMA20 (dead cross),  or RSI(14) > 70 (overbought)
- HOLD otherwise
"""
from dataclasses import dataclass

import pandas as pd

SMA_FAST = 5
SMA_SLOW = 20
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70


@dataclass
class Signal:
    action: str        # 'BUY' | 'SELL' | 'HOLD'
    reason: str
    price: float
    rsi: float
    sma_fast: float
    sma_slow: float


def compute_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def evaluate(df: pd.DataFrame) -> Signal:
    close = df["Close"]
    if len(close) < SMA_SLOW + 2:
        return Signal("HOLD", "データ不足", float(close.iloc[-1]), 50, 0, 0)

    sma_f = close.rolling(SMA_FAST).mean()
    sma_s = close.rolling(SMA_SLOW).mean()
    rsi = compute_rsi(close)

    price = float(close.iloc[-1])
    f_now, f_prev = float(sma_f.iloc[-1]), float(sma_f.iloc[-2])
    s_now, s_prev = float(sma_s.iloc[-1]), float(sma_s.iloc[-2])
    r_now = float(rsi.iloc[-1])

    golden_cross = f_prev <= s_prev and f_now > s_now
    dead_cross = f_prev >= s_prev and f_now < s_now

    if golden_cross:
        return Signal("BUY", f"ゴールデンクロス (SMA{SMA_FAST}がSMA{SMA_SLOW}を上抜け)", price, r_now, f_now, s_now)
    if r_now < RSI_OVERSOLD:
        return Signal("BUY", f"RSI={r_now:.1f} 売られすぎ (<{RSI_OVERSOLD})", price, r_now, f_now, s_now)
    if dead_cross:
        return Signal("SELL", f"デッドクロス (SMA{SMA_FAST}がSMA{SMA_SLOW}を下抜け)", price, r_now, f_now, s_now)
    if r_now > RSI_OVERBOUGHT:
        return Signal("SELL", f"RSI={r_now:.1f} 買われすぎ (>{RSI_OVERBOUGHT})", price, r_now, f_now, s_now)
    trend = "上昇トレンド" if f_now > s_now else "下降トレンド"
    return Signal("HOLD", f"シグナルなし ({trend}, RSI={r_now:.1f})", price, r_now, f_now, s_now)
