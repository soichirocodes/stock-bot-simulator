"""Bot execution: evaluate watchlist signals and execute paper trades."""
from . import db, market, strategy

MAX_POSITION_RATIO = 0.20   # cap each position at 20% of total equity
MIN_CASH_RESERVE = 0.02     # keep at least 2% of equity in cash


def _total_equity(conn, prices: dict[str, float]) -> float:
    cash = db.get_cash(conn)
    value = sum(
        p["qty"] * prices.get(p["symbol"], p["avg_cost"])
        for p in db.get_positions(conn)
    )
    return cash + value


def run_cycle() -> dict:
    """Evaluate every watchlist symbol once; execute BUY/SELL. Returns a report."""
    results = []
    with db.get_conn() as conn:
        watchlist = db.get_watchlist(conn)
        prices = market.get_last_prices(watchlist)
        equity = _total_equity(conn, prices)

        for symbol in watchlist:
            if symbol not in prices:
                results.append({"symbol": symbol, "action": "SKIP",
                                "reason": "価格データを取得できませんでした"})
                continue
            try:
                sig = strategy.evaluate(market.get_history(symbol))
            except Exception as e:
                results.append({"symbol": symbol, "action": "SKIP", "reason": str(e)})
                continue

            pos = db.get_position(conn, symbol)
            executed = False

            if sig.action == "BUY":
                cash = db.get_cash(conn)
                held_value = (pos["qty"] * sig.price) if pos else 0.0
                budget = min(
                    equity * MAX_POSITION_RATIO - held_value,
                    cash - equity * MIN_CASH_RESERVE,
                )
                qty = int(budget // sig.price)
                if qty >= 1:
                    cost = qty * sig.price
                    new_qty = (pos["qty"] if pos else 0) + qty
                    new_avg = (((pos["qty"] * pos["avg_cost"]) if pos else 0) + cost) / new_qty
                    db.set_cash(conn, cash - cost)
                    db.upsert_position(conn, symbol, new_qty, new_avg)
                    db.record_trade(conn, symbol, "BUY", qty, sig.price, None, sig.reason)
                    executed = True
                else:
                    sig.reason += " → 資金/枠不足のため見送り"

            elif sig.action == "SELL" and pos:
                qty = pos["qty"]
                proceeds = qty * sig.price
                realized = (sig.price - pos["avg_cost"]) * qty
                db.set_cash(conn, db.get_cash(conn) + proceeds)
                db.upsert_position(conn, symbol, 0, 0)
                db.record_trade(conn, symbol, "SELL", qty, sig.price, realized, sig.reason)
                executed = True

            results.append({
                "symbol": symbol,
                "action": sig.action if executed or sig.action == "HOLD" else f"{sig.action}(見送り)",
                "executed": executed,
                "price": round(sig.price, 2),
                "rsi": round(sig.rsi, 1),
                "reason": sig.reason,
            })

        equity_after = _total_equity(conn, prices)
        db.record_equity(conn, equity_after, db.get_cash(conn))

    return {"results": results, "equity": round(equity_after, 2)}
