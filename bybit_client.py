"""
bybit_client.py — Pembungkus tipis di atas pybit untuk Bybit V5 (USDT Perpetual).

Semua order dipasang di kategori "linear" (USDT perpetual futures).
"""

from __future__ import annotations

import logging
import math
from decimal import Decimal, ROUND_DOWN

from pybit.unified_trading import HTTP

log = logging.getLogger("bybit")


class BybitClient:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.session = HTTP(
            testnet=testnet,
            api_key=api_key,
            api_secret=api_secret,
        )
        self.testnet = testnet
        self._instrument_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------ util
    def instrument(self, symbol: str) -> dict | None:
        """
        Ambil spesifikasi kontrak (tick size, qty step, leverage maks).
        Kembalikan None kalau symbol tidak ada di Bybit (mis. coin cuma
        listing di Binance). Bybit melempar InvalidRequestError 10001 untuk
        symbol tak dikenal — itu ditangkap di sini supaya sinyal cukup
        di-skip, bukan membuat handler crash.
        """
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        try:
            r = self.session.get_instruments_info(category="linear", symbol=symbol)
        except Exception as e:
            if "10001" in str(e) or "symbol invalid" in str(e).lower():
                log.info("Symbol %s tidak ada di Bybit — dilewati.", symbol)
                self._instrument_cache[symbol] = None
                return None
            raise
        lst = r.get("result", {}).get("list", [])
        if not lst:
            self._instrument_cache[symbol] = None
            return None

        info = lst[0]
        spec = {
            "symbol": info["symbol"],
            "status": info.get("status"),
            "tick_size": Decimal(info["priceFilter"]["tickSize"]),
            "qty_step": Decimal(info["lotSizeFilter"]["qtyStep"]),
            "min_qty": Decimal(info["lotSizeFilter"]["minOrderQty"]),
            "max_leverage": float(info["leverageFilter"]["maxLeverage"]),
        }
        self._instrument_cache[symbol] = spec
        return spec

    @staticmethod
    def _round_step(value: float, step: Decimal) -> str:
        """Bulatkan ke bawah sesuai step size, kembalikan string (hindari float error)."""
        d = Decimal(str(value))
        q = (d / step).to_integral_value(rounding=ROUND_DOWN) * step
        return format(q.normalize(), "f")

    def round_price(self, symbol: str, price: float) -> str:
        spec = self.instrument(symbol)
        return self._round_step(price, spec["tick_size"])

    def round_qty(self, symbol: str, qty: float) -> str:
        spec = self.instrument(symbol)
        return self._round_step(qty, spec["qty_step"])

    # --------------------------------------------------------------- account
    def equity_usdt(self) -> float:
        r = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        lst = r.get("result", {}).get("list", [])
        if not lst:
            return 0.0
        coins = lst[0].get("coin", [])
        for c in coins:
            if c.get("coin") == "USDT":
                for key in ("equity", "walletBalance", "availableToWithdraw"):
                    v = c.get(key)
                    if v not in (None, ""):
                        return float(v)
        return 0.0

    def available_usdt(self) -> float:
        """
        Saldo yang BELUM dipakai sebagai margin posisi lain (free balance).
        Dipakai untuk sizing bertingkat: tiap posisi baru = persen dari sisa,
        bukan dari equity total. Jadi total margin tak pernah melebihi saldo.
        """
        r = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        lst = r.get("result", {}).get("list", [])
        if not lst:
            return 0.0
        coins = lst[0].get("coin", [])
        for c in coins:
            if c.get("coin") == "USDT":
                # Urutan preferensi: saldo bebas dulu, baru fallback ke equity
                for key in ("availableToWithdraw", "availableBalance",
                            "free", "walletBalance", "equity"):
                    v = c.get(key)
                    if v not in (None, ""):
                        return float(v)
        return 0.0

    def last_price(self, symbol: str) -> float:
        r = self.session.get_tickers(category="linear", symbol=symbol)
        return float(r["result"]["list"][0]["lastPrice"])

    def set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            self.session.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
            log.info("Leverage %s diset ke %sx", symbol, leverage)
        except Exception as e:
            # Bybit melempar error 110043 kalau leverage sudah sama — aman diabaikan
            if "110043" in str(e) or "leverage not modified" in str(e).lower():
                log.info("Leverage %s sudah %sx", symbol, leverage)
            else:
                raise

    def position(self, symbol: str) -> dict | None:
        r = self.session.get_positions(category="linear", symbol=symbol)
        for p in r.get("result", {}).get("list", []):
            if float(p.get("size", 0)) > 0:
                return p
        return None

    # ---------------------------------------------------------------- orders
    def place_limit_entry(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        order_link_id: str | None = None,
    ) -> dict:
        """Limit order entry dengan TP & SL langsung menempel pada order."""
        params = {
            "category": "linear",
            "symbol": symbol,
            "side": side,                 # "Buy" / "Sell"
            "orderType": "Limit",
            "qty": self.round_qty(symbol, qty),
            "price": self.round_price(symbol, price),
            "timeInForce": "GTC",
            "positionIdx": 0,             # one-way mode
        }
        if stop_loss or take_profit:
            params["tpslMode"] = "Full"
        if stop_loss:
            params["stopLoss"] = self.round_price(symbol, stop_loss)
            params["slTriggerBy"] = "MarkPrice"
        if take_profit:
            params["takeProfit"] = self.round_price(symbol, take_profit)
            params["tpTriggerBy"] = "MarkPrice"
        if order_link_id:
            params["orderLinkId"] = order_link_id[:36]

        log.info("Kirim entry: %s", params)
        return self.session.place_order(**params)

    def place_reduce_only_tp(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        order_link_id: str | None = None,
    ) -> dict:
        close_side = "Sell" if side == "Buy" else "Buy"
        params = {
            "category": "linear",
            "symbol": symbol,
            "side": close_side,
            "orderType": "Limit",
            "qty": self.round_qty(symbol, qty),
            "price": self.round_price(symbol, price),
            "timeInForce": "GTC",
            "reduceOnly": True,
            "positionIdx": 0,
        }
        if order_link_id:
            params["orderLinkId"] = order_link_id[:36]
        log.info("Kirim TP: %s", params)
        return self.session.place_order(**params)

    def set_stop_loss(self, symbol: str, price: float) -> dict:
        return self.session.set_trading_stop(
            category="linear",
            symbol=symbol,
            stopLoss=self.round_price(symbol, price),
            slTriggerBy="MarkPrice",
            tpslMode="Full",
            positionIdx=0,
        )

    def remove_stop_loss(self, symbol: str) -> dict:
        """Hapus SL yang sedang terpasang pada posisi (set ke 0)."""
        return self.session.set_trading_stop(
            category="linear", symbol=symbol,
            stopLoss="0", tpslMode="Full", positionIdx=0,
        )

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        return self.session.cancel_order(
            category="linear", symbol=symbol, orderId=order_id)

    def cancel_all(self, symbol: str) -> dict:
        return self.session.cancel_all_orders(category="linear", symbol=symbol)

    def open_orders(self, symbol: str) -> list[dict]:
        r = self.session.get_open_orders(category="linear", symbol=symbol)
        return r.get("result", {}).get("list", [])
