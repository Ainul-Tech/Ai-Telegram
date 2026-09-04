"""
history.py — Penyimpanan riwayat.

Dua sumber:
  1. Tabel `signals` — dicatat lokal saat bot menerima & mengeksekusi sinyal.
  2. Tabel `executions` & `closed_pnl` — DITARIK LANGSUNG dari Bybit
     (catatan buy/sell dan PnL yang sudah direalisasi), jadi angka di dashboard
     berasal dari bursa, bukan dari tebakan bot.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time

log = logging.getLogger("history")
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER,
    channel     TEXT,
    symbol      TEXT,
    side        TEXT,
    entries     TEXT,
    take_profits TEXT,
    stop_loss   REAL,
    raw_leverage INTEGER,
    used_leverage INTEGER,
    qty         REAL,
    status      TEXT,
    note        TEXT,
    raw_text    TEXT
);

CREATE TABLE IF NOT EXISTS executions (
    exec_id     TEXT PRIMARY KEY,
    ts          INTEGER,
    symbol      TEXT,
    side        TEXT,
    price       REAL,
    qty         REAL,
    fee         REAL,
    order_id    TEXT,
    order_link_id TEXT,
    exec_type   TEXT
);

CREATE TABLE IF NOT EXISTS closed_pnl (
    order_id    TEXT PRIMARY KEY,
    ts          INTEGER,
    symbol      TEXT,
    side        TEXT,
    qty         REAL,
    avg_entry   REAL,
    avg_exit    REAL,
    pnl         REAL,
    leverage    REAL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class History:
    def __init__(self, path: str):
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------- meta ---
    def get_meta(self, key: str, default=None):
        with _lock, self._conn() as c:
            r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key: str, value) -> None:
        with _lock, self._conn() as c:
            c.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    # ---------------------------------------------------------- signals ---
    def log_signal(self, sig, channel: str, status: str, note: str = "",
                   qty: float = 0.0, used_leverage: int = 0) -> int:
        with _lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO signals(ts,channel,symbol,side,entries,take_profits,"
                "stop_loss,raw_leverage,used_leverage,qty,status,note,raw_text) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    int(time.time() * 1000), channel, sig.symbol, sig.side,
                    json.dumps(sig.entries), json.dumps(sig.take_profits),
                    sig.stop_loss, sig.raw_leverage, used_leverage, qty,
                    status, note, sig.raw_text[:4000],
                ),
            )
            return cur.lastrowid

    def update_signal(self, sid: int, status: str, note: str = "") -> None:
        with _lock, self._conn() as c:
            c.execute("UPDATE signals SET status=?, note=? WHERE id=?", (status, note, sid))

    # --------------------------------------------- sinkronisasi dari Bybit --
    def sync_from_bybit(self, client, lookback_days: int = 7) -> tuple[int, int]:
        """
        Tarik catatan eksekusi (buy/sell) dan closed PnL dari Bybit.
        Ini yang dipakai dashboard sebagai sumber kebenaran.
        """
        start_ms = int((time.time() - lookback_days * 86400) * 1000)
        n_exec = n_pnl = 0

        # --- executions ---
        try:
            cursor = None
            while True:
                kw = dict(category="linear", limit=100, startTime=start_ms)
                if cursor:
                    kw["cursor"] = cursor
                r = client.session.get_executions(**kw)
                res = r.get("result", {})
                rows = res.get("list", [])
                with _lock, self._conn() as c:
                    for e in rows:
                        c.execute(
                            "INSERT OR IGNORE INTO executions"
                            "(exec_id,ts,symbol,side,price,qty,fee,order_id,"
                            "order_link_id,exec_type) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (
                                e.get("execId"), int(e.get("execTime", 0)),
                                e.get("symbol"), e.get("side"),
                                float(e.get("execPrice") or 0),
                                float(e.get("execQty") or 0),
                                float(e.get("execFee") or 0),
                                e.get("orderId"), e.get("orderLinkId"),
                                e.get("execType"),
                            ),
                        )
                        n_exec += 1
                cursor = res.get("nextPageCursor")
                if not cursor or not rows:
                    break
        except Exception as ex:
            log.error("Sync executions gagal: %s", ex)

        # --- closed pnl ---
        try:
            cursor = None
            while True:
                kw = dict(category="linear", limit=100, startTime=start_ms)
                if cursor:
                    kw["cursor"] = cursor
                r = client.session.get_closed_pnl(**kw)
                res = r.get("result", {})
                rows = res.get("list", [])
                with _lock, self._conn() as c:
                    for p in rows:
                        c.execute(
                            "INSERT OR REPLACE INTO closed_pnl"
                            "(order_id,ts,symbol,side,qty,avg_entry,avg_exit,pnl,leverage)"
                            " VALUES(?,?,?,?,?,?,?,?,?)",
                            (
                                p.get("orderId"), int(p.get("updatedTime", 0)),
                                p.get("symbol"), p.get("side"),
                                float(p.get("qty") or 0),
                                float(p.get("avgEntryPrice") or 0),
                                float(p.get("avgExitPrice") or 0),
                                float(p.get("closedPnl") or 0),
                                float(p.get("leverage") or 0),
                            ),
                        )
                        n_pnl += 1
                cursor = res.get("nextPageCursor")
                if not cursor or not rows:
                    break
        except Exception as ex:
            log.error("Sync closed PnL gagal: %s", ex)

        return n_exec, n_pnl

    # ---------------------------------------------------------- queries ---
    def recent_signals(self, limit: int = 100) -> list[dict]:
        with _lock, self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM signals ORDER BY ts DESC LIMIT ?", (limit,))]

    def recent_executions(self, limit: int = 200) -> list[dict]:
        with _lock, self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM executions ORDER BY ts DESC LIMIT ?", (limit,))]

    def recent_pnl(self, limit: int = 200) -> list[dict]:
        with _lock, self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM closed_pnl ORDER BY ts DESC LIMIT ?", (limit,))]

    def stats(self) -> dict:
        with _lock, self._conn() as c:
            rows = [dict(r) for r in c.execute("SELECT pnl FROM closed_pnl")]
        wins = [r["pnl"] for r in rows if r["pnl"] > 0]
        losses = [r["pnl"] for r in rows if r["pnl"] <= 0]
        total = sum(r["pnl"] for r in rows)
        return {
            "trades": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(rows) * 100) if rows else 0.0,
            "total_pnl": total,
            "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
            "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        }

    def equity_curve(self) -> list[dict]:
        with _lock, self._conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT ts, pnl FROM closed_pnl ORDER BY ts ASC")]
        run = 0.0
        out = []
        for r in rows:
            run += r["pnl"]
            out.append({"ts": r["ts"], "cum_pnl": round(run, 4)})
        return out
