"""Paper-trading stock simulator dashboard (Flask)."""
import bisect
from datetime import date

from flask import Flask, jsonify, render_template, request

from simulator import backtest, bot, db, market

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True  # pick up template edits without a server restart
db.init_db()


@app.after_request
def allow_cors(resp):
    # let the dashboard work even when index.html is opened outside the server
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return resp


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/portfolio")
def api_portfolio():
    with db.get_conn() as conn:
        cash = db.get_cash(conn)
        positions = db.get_positions(conn)
        symbols = [p["symbol"] for p in positions]
        prices = market.get_last_prices(symbols)

        pos_list = []
        total_value = 0.0
        for p in positions:
            price = prices.get(p["symbol"], p["avg_cost"])
            value = p["qty"] * price
            total_value += value
            pos_list.append({
                "symbol": p["symbol"],
                "qty": p["qty"],
                "avg_cost": round(p["avg_cost"], 2),
                "price": round(price, 2),
                "value": round(value, 2),
                "unrealized_pnl": round((price - p["avg_cost"]) * p["qty"], 2),
                "pnl_pct": round((price / p["avg_cost"] - 1) * 100, 2) if p["avg_cost"] else 0,
            })

        equity = cash + total_value
        realized = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS s FROM trades"
        ).fetchone()["s"]

    return jsonify({
        "cash": round(cash, 2),
        "positions": pos_list,
        "equity": round(equity, 2),
        "initial_cash": db.INITIAL_CASH,
        "total_pnl": round(equity - db.INITIAL_CASH, 2),
        "total_pnl_pct": round((equity / db.INITIAL_CASH - 1) * 100, 2),
        "realized_pnl": round(realized, 2),
    })


@app.route("/api/trades")
def api_trades():
    with db.get_conn() as conn:
        trades = [dict(t) for t in db.get_trades(conn)]
    return jsonify(trades)


@app.route("/api/equity_history")
def api_equity_history():
    with db.get_conn() as conn:
        rows = [dict(r) for r in db.get_equity_history(conn)]
    # benchmark: what the same initial cash would be worth in SPY (buy & hold)
    for r in rows:
        r["benchmark"] = None
    if rows:
        try:
            closes = market.get_history("SPY")["Close"]
            days = [d.date() for d in closes.index]

            def spy_close_on(ts: str) -> float | None:
                i = bisect.bisect_right(days, date.fromisoformat(ts[:10])) - 1
                return float(closes.iloc[i]) if i >= 0 else None

            base = spy_close_on(rows[0]["ts"])
            if base:
                for r in rows:
                    c = spy_close_on(r["ts"])
                    if c:
                        r["benchmark"] = round(db.INITIAL_CASH * c / base, 2)
        except Exception:
            pass  # benchmark is best-effort; the dashboard works without it
    return jsonify(rows)


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    body = request.json or {}
    with db.get_conn() as conn:
        symbols = db.get_watchlist(conn)
    try:
        result = backtest.run(symbols, body.get("period", "5y"),
                              body.get("strategy", "sma_rsi"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route("/api/backtest/compare", methods=["POST"])
def api_backtest_compare():
    period = (request.json or {}).get("period", "5y")
    with db.get_conn() as conn:
        symbols = db.get_watchlist(conn)
    try:
        result = backtest.compare(symbols, period)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route("/api/watchlist", methods=["GET", "POST", "DELETE"])
def api_watchlist():
    with db.get_conn() as conn:
        if request.method == "POST":
            symbol = (request.json or {}).get("symbol", "").strip().upper()
            if not symbol:
                return jsonify({"error": "symbol is required"}), 400
            try:
                market.get_last_price(symbol)  # validate before adding
            except Exception:
                return jsonify({"error": f"'{symbol}' の価格データを取得できません"}), 400
            db.add_watch(conn, symbol)
        elif request.method == "DELETE":
            symbol = (request.json or {}).get("symbol", "").strip().upper()
            db.remove_watch(conn, symbol)
        return jsonify({"watchlist": db.get_watchlist(conn)})


@app.route("/api/bot/run", methods=["POST"])
def api_bot_run():
    try:
        report = bot.run_cycle()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(report)


@app.route("/api/chart/<symbol>")
def api_chart(symbol):
    try:
        df = market.get_history(symbol.upper())
    except Exception as e:
        return jsonify({"error": str(e)}), 404
    from simulator.strategy import SMA_FAST, SMA_SLOW, compute_rsi
    close = df["Close"]
    return jsonify({
        "dates": [d.strftime("%Y-%m-%d") for d in df.index],
        "close": [round(float(v), 2) for v in close],
        "sma_fast": [None if v != v else round(float(v), 2) for v in close.rolling(SMA_FAST).mean()],
        "sma_slow": [None if v != v else round(float(v), 2) for v in close.rolling(SMA_SLOW).mean()],
        "rsi": [round(float(v), 1) for v in compute_rsi(close)],
    })


@app.route("/api/reset", methods=["POST"])
def api_reset():
    db.reset_db()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)
