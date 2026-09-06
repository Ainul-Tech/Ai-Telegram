"""
tests/mock_bybit.py — Bybit tiruan untuk menguji alur penuh tanpa uang & tanpa jaringan.

Mensimulasikan: limit order matching, posisi, SL/TP, reduce-only,
catatan eksekusi, dan closed PnL. Cukup untuk memverifikasi logika bot.
"""

from __future__ import annotations

import itertools
import time


class MockSession:
    """Meniru pybit.unified_trading.HTTP secukupnya."""

    FEE_MAKER = 0.0002   # 0,02%
    FEE_TAKER = 0.00055  # 0,055%

    def __init__(self, equity: float = 20.0, max_leverage: str = "50"):
        self.max_leverage = max_leverage
        self.invalid_symbols = set()   # symbol yg dianggap tidak ada di Bybit
        self.equity = equity
        self.price: dict[str, float] = {}
        self.orders: list[dict] = []
        self.positions: dict[str, dict] = {}
        self.executions: list[dict] = []
        self.closed: list[dict] = []
        self.leverage: dict[str, int] = {}
        self._ids = itertools.count(1)
        self.log: list[str] = []

    # ------------------------------------------------------------ helpers
    def _oid(self) -> str:
        return f"ord{next(self._ids):04d}"

    def set_price(self, symbol: str, px: float) -> None:
        """Gerakkan harga, lalu jalankan matching engine."""
        self.price[symbol] = px
        self._match(symbol)

    def _record_exec(self, symbol, side, px, qty, fee, oid, link, etype):
        self.executions.append({
            "execId": f"x{len(self.executions)+1}", "execTime": str(int(time.time()*1000)),
            "symbol": symbol, "side": side, "execPrice": str(px), "execQty": str(qty),
            "execFee": str(fee), "orderId": oid, "orderLinkId": link, "execType": etype,
        })

    # ------------------------------------------------------ matching engine
    def _match(self, symbol: str) -> None:
        px = self.price[symbol]
        pos = self.positions.get(symbol)

        # 1. Stop loss lebih dulu (konservatif)
        if pos and pos["size"] > 0 and pos.get("stopLoss"):
            sl = pos["stopLoss"]
            hit = (pos["side"] == "Buy" and px <= sl) or (pos["side"] == "Sell" and px >= sl)
            if hit:
                self.log.append(f"SL KENA {symbol} @ {sl}")
                self._close(symbol, sl, pos["size"], "SL", taker=True)
                self._cancel_reduce_only(symbol)
                return

        # 2. Limit order
        for o in list(self.orders):
            if o["symbol"] != symbol or o["status"] != "New":
                continue
            p = o["price"]
            fill = (o["side"] == "Buy" and px <= p) or (o["side"] == "Sell" and px >= p)
            if not fill:
                continue
            o["status"] = "Filled"
            if o.get("reduceOnly"):
                cur = self.positions.get(symbol)
                if not cur or cur["size"] <= 0:
                    continue
                q = min(o["qty"], cur["size"])
                self.log.append(f"TP KENA {symbol} @ {p} qty={q} ({o['orderLinkId']})")
                self._close(symbol, p, q, o["orderLinkId"], taker=False)
            else:
                self._open(symbol, o["side"], p, o["qty"], o.get("stopLoss"), o["orderLinkId"])

    def _open(self, symbol, side, px, qty, sl, link):
        pos = self.positions.get(symbol)
        fee = px * qty * self.FEE_MAKER
        self.equity -= fee
        if not pos or pos["size"] == 0:
            self.positions[symbol] = {
                "symbol": symbol, "side": side, "size": qty, "avgPrice": px,
                "stopLoss": sl, "entryFeePaid": fee,
            }
        else:
            tot = pos["size"] + qty
            pos["avgPrice"] = (pos["avgPrice"] * pos["size"] + px * qty) / tot
            pos["size"] = tot
            if sl:
                pos["stopLoss"] = sl
            pos["entryFeePaid"] = pos.get("entryFeePaid", 0) + fee
        self.log.append(f"ENTRY TERISI {symbol} {side} @ {px} qty={qty}")
        self._record_exec(symbol, side, px, qty, fee, self._oid(), link, "Trade")

    def _close(self, symbol, px, qty, link, taker=False):
        pos = self.positions[symbol]
        entry = pos["avgPrice"]
        sign = 1 if pos["side"] == "Buy" else -1
        gross = (px - entry) * qty * sign
        fee = px * qty * (self.FEE_TAKER if taker else self.FEE_MAKER)
        self.equity += gross - fee
        pos["size"] -= qty
        close_side = "Sell" if pos["side"] == "Buy" else "Buy"
        self._record_exec(symbol, close_side, px, qty, fee, self._oid(), link, "Trade")
        self.closed.append({
            "orderId": self._oid(), "updatedTime": str(int(time.time()*1000)),
            "symbol": symbol, "side": close_side, "qty": str(qty),
            "avgEntryPrice": str(entry), "avgExitPrice": str(px),
            "closedPnl": str(round(gross - fee, 8)),
            "leverage": str(self.leverage.get(symbol, 10)),
        })
        if pos["size"] <= 1e-12:
            self.positions.pop(symbol, None)
            self._cancel_reduce_only(symbol)

    def _cancel_reduce_only(self, symbol):
        for o in self.orders:
            if o["symbol"] == symbol and o["status"] == "New" and o.get("reduceOnly"):
                o["status"] = "Cancelled"

    # ------------------------------------------------------------- API ---
    def get_instruments_info(self, category, symbol):
        if symbol in self.invalid_symbols:
            raise Exception("params error: symbol invalid (ErrCode: 10001)")
        return {"result": {"list": [{
            "symbol": symbol, "status": "Trading",
            "priceFilter": {"tickSize": "0.000001"},
            "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1"},
            "leverageFilter": {"maxLeverage": self.max_leverage},
        }]}}

    def get_wallet_balance(self, accountType, coin=None):
        return {"result": {"list": [{"coin": [{"coin": "USDT",
                "equity": str(self.equity), "walletBalance": str(self.equity)}]}]}}

    def get_tickers(self, category, symbol):
        return {"result": {"list": [{"lastPrice": str(self.price.get(symbol, 0))}]}}

    def set_leverage(self, category, symbol, buyLeverage, sellLeverage):
        self.leverage[symbol] = int(buyLeverage)
        return {"retCode": 0}

    def get_positions(self, category, symbol):
        p = self.positions.get(symbol)
        if not p:
            return {"result": {"list": []}}
        return {"result": {"list": [{
            "symbol": symbol, "side": p["side"], "size": str(p["size"]),
            "avgPrice": str(p["avgPrice"]),
        }]}}

    def place_order(self, **kw):
        o = {
            "orderId": self._oid(), "symbol": kw["symbol"], "side": kw["side"],
            "price": float(kw["price"]), "qty": float(kw["qty"]),
            "reduceOnly": kw.get("reduceOnly", False),
            "stopLoss": float(kw["stopLoss"]) if kw.get("stopLoss") else None,
            "orderLinkId": kw.get("orderLinkId", ""), "status": "New",
        }
        self.orders.append(o)
        self.log.append(
            f"ORDER {'TP' if o['reduceOnly'] else 'ENTRY'} {o['symbol']} "
            f"{o['side']} qty={o['qty']} @ {o['price']}"
            + (f" SL={o['stopLoss']}" if o["stopLoss"] else "")
        )
        # Order langsung kena kalau harga sudah lewat
        if o["symbol"] in self.price:
            self._match(o["symbol"])
        return {"retCode": 0, "result": {"orderId": o["orderId"]}}

    def get_open_orders(self, category, symbol):
        return {"result": {"list": [
            {"orderId": o["orderId"], "reduceOnly": o["reduceOnly"],
             "orderLinkId": o["orderLinkId"]}
            for o in self.orders if o["symbol"] == symbol and o["status"] == "New"]}}

    def cancel_order(self, category, symbol, orderId):
        for o in self.orders:
            if o["orderId"] == orderId and o["status"] == "New":
                o["status"] = "Cancelled"
        return {"retCode": 0}

    def cancel_all_orders(self, category, symbol):
        for o in self.orders:
            if o["symbol"] == symbol and o["status"] == "New":
                o["status"] = "Cancelled"
        return {"retCode": 0}

    def set_trading_stop(self, category, symbol, stopLoss=None, **kw):
        pos = self.positions.get(symbol)
        if not pos:
            return {"retCode": 0}
        if stopLoss in (None, "0", 0):
            pos["stopLoss"] = None
            self.log.append(f"SL {symbol} DIHAPUS")
        else:
            pos["stopLoss"] = float(stopLoss)
            self.log.append(f"SL {symbol} DISET -> {stopLoss}")
        return {"retCode": 0}

    def get_executions(self, category, limit=100, startTime=None, cursor=None):
        return {"result": {"list": self.executions, "nextPageCursor": ""}}

    def get_closed_pnl(self, category, limit=100, startTime=None, cursor=None):
        return {"result": {"list": self.closed, "nextPageCursor": ""}}
