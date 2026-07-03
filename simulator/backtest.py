"""Historical backtest engine with pluggable strategies.

Every strategy runs under identical, reality-leaning conditions so that
results are directly comparable:
- signals decided on day t's close, filled at day t+1's OPEN
- commission charged on every fill
- same money management (per-position cap + cash reserve)

Strategies produce buy/sell signal frames only; the engine owns execution.
"""
import pandas as pd

from . import market
from .bot import MAX_POSITION_RATIO, MIN_CASH_RESERVE
from .strategy import RSI_OVERBOUGHT, RSI_OVERSOLD, SMA_FAST, SMA_SLOW, compute_rsi

COMMISSION_RATE = 0.001  # 0.1% per side
BENCHMARK = "SPY"
PERIODS = ("1y", "2y", "5y", "10y")


# ── data loading ──────────────────────────────────────────────

def _load(symbol: str, period: str) -> pd.DataFrame:
    df = market.get_history(symbol, period)
    out = pd.DataFrame({"open": df["Open"], "close": df["Close"]})
    idx = out.index
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    return out


# ── strategies: dict[symbol -> price df] → dict[symbol -> bool df(buy, sell)] ──

def _bool_frame(index) -> pd.DataFrame:
    return pd.DataFrame({"buy": False, "sell": False}, index=index)


def _signals_sma_rsi(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Golden/dead cross on SMA5/20, plus RSI(14) 30/70 mean reversion."""
    out = {}
    for sym, df in data.items():
        close = df["close"]
        sma_f = close.rolling(SMA_FAST).mean()
        sma_s = close.rolling(SMA_SLOW).mean()
        rsi = compute_rsi(close)
        golden = (sma_f.shift(1) <= sma_s.shift(1)) & (sma_f > sma_s)
        dead = (sma_f.shift(1) >= sma_s.shift(1)) & (sma_f < sma_s)
        valid = sma_s.notna()
        sig = _bool_frame(df.index)
        sig["buy"] = (golden | (rsi < RSI_OVERSOLD)) & valid
        sig["sell"] = (dead | (rsi > RSI_OVERBOUGHT)) & valid
        out[sym] = sig.fillna(False)
    return out


def _signals_momentum(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Cross-sectional 12-1 momentum: monthly, hold the top half (positive only)."""
    LOOKBACK, SKIP, REBALANCE = 252, 21, 21
    closes = pd.DataFrame({s: d["close"] for s, d in data.items()})
    mom = closes.shift(SKIP) / closes.shift(LOOKBACK) - 1
    top_n = max(1, len(closes.columns) // 2)

    out = {s: _bool_frame(closes.index) for s in closes.columns}
    for i in range(0, len(closes.index), REBALANCE):
        row = mom.iloc[i].dropna()
        row = row[row > 0]
        if row.empty:
            continue  # nothing with positive momentum: engine keeps holdings
        winners = set(row.nlargest(top_n).index)
        day = closes.index[i]
        for s in closes.columns:
            out[s].at[day, "buy" if s in winners else "sell"] = True
    return out


def _signals_donchian(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Donchian channel breakout: buy 20-day high break, exit 10-day low break."""
    ENTRY, EXIT = 20, 10
    out = {}
    for sym, df in data.items():
        close = df["close"]
        hi = close.rolling(ENTRY).max().shift(1)
        lo = close.rolling(EXIT).min().shift(1)
        sig = _bool_frame(df.index)
        sig["buy"] = close > hi
        sig["sell"] = close < lo
        out[sym] = sig.fillna(False)
    return out


def _signals_buy_hold(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Buy every symbol up to the position cap, top up monthly, never sell."""
    TOPUP = 21
    out = {}
    for sym, df in data.items():
        sig = _bool_frame(df.index)
        sig.iloc[::TOPUP, sig.columns.get_loc("buy")] = True
        out[sym] = sig
    return out


STRATEGIES = {
    "sma_rsi": {"label": "SMAクロス+RSI (現行)", "fn": _signals_sma_rsi},
    "momentum": {"label": "モメンタム (12-1ヶ月・月次)", "fn": _signals_momentum},
    "donchian": {"label": "ブレイクアウト (Donchian 20/10)", "fn": _signals_donchian},
    "buy_hold": {"label": "バイ&ホールド (均等・月次積増)", "fn": _signals_buy_hold},
}


# ── engine ────────────────────────────────────────────────────

def _simulate(data: dict[str, pd.DataFrame], signals: dict[str, pd.DataFrame],
              calendar: pd.DatetimeIndex, initial_cash: float) -> dict:
    cash = initial_cash
    positions: dict[str, tuple[int, float]] = {}   # symbol -> (qty, avg_cost incl. fee)
    last_close: dict[str, float] = {}
    pending: list[tuple[str, str]] = []

    equity_curve: list[float] = []
    n_buys = n_sells = wins = 0
    gross_profit = gross_loss = total_commission = 0.0

    def price_at(sym: str, day, col: str) -> float | None:
        df = data[sym]
        if day in df.index:
            v = df.at[day, col]
            if not pd.isna(v):
                return float(v)
        return None

    def mark_to_market() -> float:
        return cash + sum(q * last_close.get(s, ac) for s, (q, ac) in positions.items())

    for day in calendar:
        # 1) fill yesterday's orders at today's open
        for sym, action in pending:
            o = price_at(sym, day, "open")
            if o is None:
                continue
            if action == "BUY":
                equity = mark_to_market()
                held = positions.get(sym, (0, 0.0))[0] * o
                budget = min(equity * MAX_POSITION_RATIO - held,
                             cash - equity * MIN_CASH_RESERVE)
                qty = int(budget // (o * (1 + COMMISSION_RATE)))
                if qty >= 1:
                    fee = qty * o * COMMISSION_RATE
                    q0, ac0 = positions.get(sym, (0, 0.0))
                    positions[sym] = (q0 + qty, (q0 * ac0 + qty * o + fee) / (q0 + qty))
                    cash -= qty * o + fee
                    total_commission += fee
                    n_buys += 1
            elif action == "SELL" and sym in positions:
                qty, ac = positions.pop(sym)
                fee = qty * o * COMMISSION_RATE
                cash += qty * o - fee
                total_commission += fee
                pnl = qty * o - fee - qty * ac
                n_sells += 1
                if pnl >= 0:
                    wins += 1
                    gross_profit += pnl
                else:
                    gross_loss += -pnl
        pending = []

        # 2) mark to market at close, queue tomorrow's orders
        for sym in data:
            c = price_at(sym, day, "close")
            if c is not None:
                last_close[sym] = c
            sig = signals[sym]
            if day in sig.index:
                if sig.at[day, "buy"]:
                    pending.append((sym, "BUY"))
                elif sig.at[day, "sell"] and sym in positions:
                    pending.append((sym, "SELL"))

        equity_curve.append(mark_to_market())

    eq = pd.Series(equity_curve, index=calendar)
    daily_ret = eq.pct_change().dropna()
    std = float(daily_ret.std() or 0)
    final_equity = equity_curve[-1]

    return {
        "equity": [round(v, 2) for v in equity_curve],
        "summary": {
            "final_equity": round(final_equity, 2),
            "total_return_pct": round((final_equity / initial_cash - 1) * 100, 2),
            "num_buys": n_buys,
            "num_sells": n_sells,
            "win_rate_pct": round(wins / n_sells * 100, 1) if n_sells else None,
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
            "max_drawdown_pct": round(float((eq / eq.cummax() - 1).min()) * 100, 2),
            "sharpe": round(float(daily_ret.mean()) / std * (252 ** 0.5), 2) if std > 0 else 0.0,
            "total_commission": round(total_commission, 2),
        },
    }


def _load_universe(symbols: list[str], period: str):
    bench = _load(BENCHMARK, period)
    data: dict[str, pd.DataFrame] = {}
    skipped: list[str] = []
    for s in symbols:
        try:
            data[s] = _load(s, period)
        except Exception:
            skipped.append(s)
    if not data:
        raise ValueError("ウォッチリスト銘柄の価格データを取得できませんでした")
    return bench, data, skipped


def compare(symbols: list[str], period: str = "5y",
            initial_cash: float = 100_000.0) -> dict:
    """Run every registered strategy on the same data and money management."""
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    bench, data, skipped = _load_universe(symbols, period)
    calendar = bench.index
    dates = [d.strftime("%Y-%m-%d") for d in calendar]

    spy_close = bench["close"].dropna()
    spy_curve = bench["close"].ffill() / spy_close.iloc[0] * initial_cash

    results = []
    for name, spec in STRATEGIES.items():
        sim = _simulate(data, spec["fn"](data), calendar, initial_cash)
        results.append({"name": name, "label": spec["label"], **sim})

    return {
        "period": period,
        "start": dates[0],
        "end": dates[-1],
        "symbols": sorted(data.keys()),
        "skipped": skipped,
        "initial_cash": initial_cash,
        "commission_rate_pct": COMMISSION_RATE * 100,
        "spy_return_pct": round(float(spy_close.iloc[-1] / spy_close.iloc[0] - 1) * 100, 2),
        "dates": dates,
        "spy_curve": [round(float(v), 2) for v in spy_curve],
        "strategies": results,
    }


def run(symbols: list[str], period: str = "5y", strategy: str = "sma_rsi",
        initial_cash: float = 100_000.0) -> dict:
    """Single-strategy backtest (kept for the /api/backtest endpoint)."""
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {tuple(STRATEGIES)}")
    bench, data, skipped = _load_universe(symbols, period)
    calendar = bench.index
    dates = [d.strftime("%Y-%m-%d") for d in calendar]

    spy_close = bench["close"].dropna()
    spy_curve = bench["close"].ffill() / spy_close.iloc[0] * initial_cash
    sim = _simulate(data, STRATEGIES[strategy]["fn"](data), calendar, initial_cash)

    return {
        "summary": {
            "period": period,
            "strategy": strategy,
            "symbols": sorted(data.keys()),
            "skipped": skipped,
            "start": dates[0],
            "end": dates[-1],
            "initial_cash": initial_cash,
            "spy_return_pct": round(float(spy_close.iloc[-1] / spy_close.iloc[0] - 1) * 100, 2),
            "commission_rate_pct": COMMISSION_RATE * 100,
            **sim["summary"],
        },
        "curve": {
            "dates": dates,
            "equity": sim["equity"],
            "spy": [round(float(v), 2) for v in spy_curve],
        },
    }
