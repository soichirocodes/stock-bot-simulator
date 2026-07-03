"""SQLite persistence layer for the paper-trading simulator."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "simulator.db"

INITIAL_CASH = 100_000.0  # virtual USD

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA", "AMZN", "META"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    qty INTEGER NOT NULL,
    avg_cost REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    qty INTEGER NOT NULL,
    price REAL NOT NULL,
    amount REAL NOT NULL,
    realized_pnl REAL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS equity_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    equity REAL NOT NULL,
    cash REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT cash FROM portfolio WHERE id = 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO portfolio (id, cash) VALUES (1, ?)", (INITIAL_CASH,))
        if conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO watchlist (symbol) VALUES (?)",
                [(s,) for s in DEFAULT_WATCHLIST],
            )


def reset_db() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM positions")
        conn.execute("DELETE FROM trades")
        conn.execute("DELETE FROM equity_history")
        conn.execute("UPDATE portfolio SET cash = ? WHERE id = 1", (INITIAL_CASH,))


def get_cash(conn: sqlite3.Connection) -> float:
    return conn.execute("SELECT cash FROM portfolio WHERE id = 1").fetchone()["cash"]


def set_cash(conn: sqlite3.Connection, cash: float) -> None:
    conn.execute("UPDATE portfolio SET cash = ? WHERE id = 1", (cash,))


def get_positions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM positions ORDER BY symbol").fetchall()


def get_position(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM positions WHERE symbol = ?", (symbol,)).fetchone()


def upsert_position(conn: sqlite3.Connection, symbol: str, qty: int, avg_cost: float) -> None:
    if qty <= 0:
        conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
    else:
        conn.execute(
            "INSERT INTO positions (symbol, qty, avg_cost) VALUES (?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET qty = excluded.qty, avg_cost = excluded.avg_cost",
            (symbol, qty, avg_cost),
        )


def record_trade(conn: sqlite3.Connection, symbol: str, side: str, qty: int,
                 price: float, realized_pnl: float | None, reason: str) -> None:
    conn.execute(
        "INSERT INTO trades (ts, symbol, side, qty, price, amount, realized_pnl, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now_iso(), symbol, side, qty, price, qty * price, realized_pnl, reason),
    )


def get_trades(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def record_equity(conn: sqlite3.Connection, equity: float, cash: float) -> None:
    conn.execute(
        "INSERT INTO equity_history (ts, equity, cash) VALUES (?, ?, ?)",
        (now_iso(), equity, cash),
    )


def get_equity_history(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM equity_history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return list(reversed(rows))


def get_watchlist(conn: sqlite3.Connection) -> list[str]:
    return [r["symbol"] for r in conn.execute("SELECT symbol FROM watchlist ORDER BY symbol")]


def add_watch(conn: sqlite3.Connection, symbol: str) -> None:
    conn.execute("INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (symbol.upper(),))


def remove_watch(conn: sqlite3.Connection, symbol: str) -> None:
    conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
