"""
trade_manager.py — Mengubah Signal menjadi order nyata di Bybit.

Aturan yang dipakai (sesuai spesifikasi):
  [2] Entry persis di harga sinyal — tidak ada penyesuaian slippage.
  [3] TP & SL menempel langsung pada order entry.
  [4] Leverage selalu cfg.LEVERAGE (10x), angka dari sinyal diabaikan.
  [5] Margin per sinyal = cfg.ENTRY_EQUITY_PERCENT (10%) dari equity.
  [6] Hanya TP1 & TP2 dipakai, 50% : 50%. TP3/TP4 dibuang.
  [7] Saat TP1 kena → SL lama dihapus, diset ulang ke harga entry.
  [8] Bot berhenti total kalau equity turun > cfg.MAX_DRAWDOWN_PERCENT.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import uuid

from bybit_client import BybitClient
from config import cfg
from history import History
from signal_parser import Signal

log = logging.getLogger("trade")


class CircuitBreaker(Exception):
    """Dilempar saat batas kerugian tercapai."""


class TradeManager:
    def __init__(self, client: BybitClient, hist: History):
        self.client = client
        self.hist = hist
        self.active: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.halted = False

        # [8] Equity awal dipakai sebagai patokan drawdown. Disimpan permanen
        #     supaya restart bot tidak mereset patokan.
        stored = self.hist.get_meta("start_equity")
        if stored is None:
            eq = self.client.equity_usdt()
            self.hist.set_meta("start_equity", eq)
            self.start_equity = eq
        else:
            self.start_equity = float(stored)
        log.info("Equity patokan drawdown: %.2f USDT", self.start_equity)

    # -------------------------------------------------- circuit breaker [8]
    def check_drawdown(self) -> None:
        eq = self.client.equity_usdt()
        if self.start_equity <= 0:
            return
        dd = (self.start_equity - eq) / self.start_equity * 100
        if dd >= cfg.MAX_DRAWDOWN_PERCENT:
            self.halted = True
            self.hist.set_meta("halted_at", int(time.time()))
            self.hist.set_meta("halted_equity", eq)
            log.critical(
                "CIRCUIT BREAKER: equity %.2f (turun %.1f%% dari %.2f). "
                "Bot BERHENTI menerima sinyal baru.",
                eq, dd, self.start_equity,
            )
            raise CircuitBreaker(f"drawdown {dd:.1f}%")

    # ------------------------------------------------- pemilihan entry
    def select_entries(self, sig: Signal) -> tuple[list[float], list[float], str]:
        """
        Tentukan entry mana yang dipakai dan porsinya.
        Kembalikan (daftar_harga, daftar_porsi, penjelasan).
        """
        mode = cfg.ENTRY_MODE

        if len(sig.entries) == 1 or mode == "first":
            return [sig.entries[0]], [1.0], f"entry pertama ({sig.entries[0]})"

        if mode == "split":
            n = len(sig.entries)
            splits = cfg.ENTRY_SPLIT[:n]
            tot = sum(splits) or 1.0
            return sig.entries, [x / tot for x in splits], "semua entry dibagi rata"

        # mode "nearest": satu order saja. Logika lengkap sesuai spesifikasi:
        #  1) pilih entry sinyal yang paling dekat harga pasar
        #  2) bandingkan harga pasar dg entry terpilih:
        #     - pasar <= entry (harga lebih murah/sama)  -> pakai entry info (limit)
        #     - pasar > entry (harga lebih mahal):
        #         * selisih > ENTRY_CHASE_MAX_PERCENT -> tetap pakai entry info (limit,
        #           tunggu harga turun; kemungkinan tidak terisi -> aman)
        #         * selisih <= ENTRY_CHASE_MAX_PERCENT -> kejar: entry = harga pasar
        try:
            last = self.client.last_price(sig.symbol)
        except Exception as e:
            log.warning("Gagal ambil harga %s (%s), pakai entry pertama", sig.symbol, e)
            return [sig.entries[0]], [1.0], f"entry pertama ({sig.entries[0]}, harga gagal dibaca)"

        chosen = min(sig.entries, key=lambda e: abs(e - last))
        others = [e for e in sig.entries if e != chosen]

        if sig.side == "Buy":
            # Untuk LONG: "lebih mahal" = harga pasar di ATAS entry
            if last <= chosen:
                note = (f"harga pasar {last:.8f} <= entry {chosen} -> "
                        f"pakai entry info (limit). diabaikan: {others}")
                return [chosen], [1.0], note
            diff_pct = (last - chosen) / chosen * 100
            if diff_pct > cfg.ENTRY_CHASE_MAX_PERCENT:
                note = (f"harga pasar {last:.8f} lebih mahal {diff_pct:.2f}% "
                        f"(> {cfg.ENTRY_CHASE_MAX_PERCENT}%) -> tetap entry info {chosen} "
                        f"(limit, tunggu turun)")
                return [chosen], [1.0], note
            note = (f"harga pasar {last:.8f} lebih mahal {diff_pct:.2f}% "
                    f"(<= {cfg.ENTRY_CHASE_MAX_PERCENT}%) -> KEJAR di harga pasar {last:.8f}")
            return [last], [1.0], note
        else:
            # Untuk SHORT: "lebih mahal untuk kita" = harga pasar di BAWAH entry
            if last >= chosen:
                note = (f"harga pasar {last:.8f} >= entry {chosen} -> "
                        f"pakai entry info (limit). diabaikan: {others}")
                return [chosen], [1.0], note
            diff_pct = (chosen - last) / chosen * 100
            if diff_pct > cfg.ENTRY_CHASE_MAX_PERCENT:
                note = (f"harga pasar {last:.8f} lebih rendah {diff_pct:.2f}% "
                        f"(> {cfg.ENTRY_CHASE_MAX_PERCENT}%) -> tetap entry info {chosen} "
                        f"(limit, tunggu naik)")
                return [chosen], [1.0], note
            note = (f"harga pasar {last:.8f} lebih rendah {diff_pct:.2f}% "
                    f"(<= {cfg.ENTRY_CHASE_MAX_PERCENT}%) -> KEJAR di harga pasar {last:.8f}")
            return [last], [1.0], note

    # --------------------------------------------------------- sizing [5]
    def compute_size(self, sig: Signal,
                     entries: list[float] | None = None,
                     splits: list[float] | None = None) -> tuple[float, float, dict]:
        equity = self.client.equity_usdt()
        if equity <= 0:
            raise RuntimeError("Equity USDT 0 — cek akun / permission API")

        # Margin = persen dari SISA SALDO (available), bukan dari equity total.
        # Efeknya sizing bertingkat: posisi 1 ambil 25% dari saldo penuh,
        # posisi 2 ambil 25% dari sisa, dst. Total margin tak pernah > saldo.
        # Contoh $20: p1=5, p2=3.75, p3=2.81, ...
        try:
            available = self.client.available_usdt()
        except Exception:
            available = equity
        if available <= 0:
            available = equity

        entries = entries or sig.entries
        splits = splits or [1.0 / len(entries)] * len(entries)
        avg_entry = sum(e * w for e, w in zip(entries, splits)) / (sum(splits) or 1.0)

        margin = available * (cfg.ENTRY_EQUITY_PERCENT / 100.0)
        notional = margin * cfg.LEVERAGE
        qty = notional / avg_entry

        sl_dist = abs(avg_entry - sig.stop_loss)
        info = {
            "equity": equity,
            "available": available,
            "margin": margin,
            "notional": notional,
            "sl_percent": sl_dist / avg_entry * 100,
            "risk_if_sl": (qty * sl_dist) / equity * 100,
        }
        return qty, avg_entry, info

    # ------------------------------------------------------------ filters
    def reconcile(self) -> None:
        """
        Samakan daftar posisi internal bot dengan kondisi NYATA di Bybit.
        Kalau kamu force-close posisi manual di Bybit, entri di self.active
        untuk symbol itu dihapus, order sisa (TP/SL) dibatalkan, monitornya
        berhenti sendiri. Dipanggil sebelum tiap sinyal & berkala oleh monitor.
        """
        with self._lock:
            symbols = list(self.active.keys())
        for sym in symbols:
            try:
                pos = self.client.position(sym)
            except Exception:
                continue  # jangan hapus kalau gagal cek (bisa jaringan)
            st = self.active.get(sym)
            if not st:
                continue
            # Posisi hilang di Bybit padahal bot pernah melihatnya terisi
            if not pos and st.get("max_size_seen", 0) > 0:
                log.info("RECONCILE: posisi %s tidak ada lagi di Bybit "
                         "(kemungkinan ditutup manual). Membersihkan.", sym)
                try:
                    self.client.cancel_all(sym)
                except Exception:
                    pass
                self.hist.update_signal(st["sid"], "CLOSED_MANUAL",
                                        "ditutup di Bybit / reconcile")
                with self._lock:
                    self.active.pop(sym, None)

    def pre_checks(self, sig: Signal) -> str | None:
        if self.halted:
            return "bot dalam status HALTED (circuit breaker)"
        if not sig.is_valid():
            return "struktur sinyal tidak logis (TP/SL di sisi yang salah)"
        if cfg.SYMBOL_WHITELIST and sig.symbol not in cfg.SYMBOL_WHITELIST:
            return f"{sig.symbol} tidak ada di whitelist"

        spec = self.client.instrument(sig.symbol)
        if not spec:
            return f"{sig.symbol} tidak tersedia di Bybit linear perpetual"
        if spec.get("status") != "Trading":
            return f"{sig.symbol} berstatus {spec.get('status')}"

        # Samakan dulu dg kondisi nyata Bybit (tangani force-close manual)
        self.reconcile()

        with self._lock:
            if sig.symbol in self.active:
                return f"sudah ada posisi/order aktif untuk {sig.symbol}"
            if len(self.active) >= cfg.MAX_CONCURRENT_POSITIONS:
                return f"batas {cfg.MAX_CONCURRENT_POSITIONS} posisi bersamaan tercapai"
        # Tidak ada pengecekan slippage — entry mengikuti harga sinyal apa adanya.

        # Opsional: tolak sinyal dengan SL sangat jauh (kerugian per trade membengkak)
        if cfg.MAX_SL_PERCENT > 0 and sig.stop_loss:
            avg = sum(sig.entries) / len(sig.entries)
            slp = abs(avg - sig.stop_loss) / avg * 100
            if slp > cfg.MAX_SL_PERCENT:
                return f"jarak SL {slp:.1f}% melebihi batas {cfg.MAX_SL_PERCENT}%"
        return None

    # ------------------------------------------------------------ execute
    def execute(self, sig: Signal, channel: str = "") -> None:
        try:
            self.check_drawdown()
        except CircuitBreaker:
            self.hist.log_signal(sig, channel, "HALTED", "circuit breaker aktif")
            return

        reason = self.pre_checks(sig)
        if reason:
            log.warning("SINYAL DILEWATI (%s): %s", sig.symbol, reason)
            self.hist.log_signal(sig, channel, "SKIPPED", reason)
            return

        entries, splits, entry_note = self.select_entries(sig)
        qty, avg_entry, info = self.compute_size(sig, entries, splits)
        spec = self.client.instrument(sig.symbol)

        # [6] Buang TP3 dan seterusnya
        tps = sig.take_profits[: cfg.TP_COUNT]
        if len(tps) < cfg.TP_COUNT:
            msg = f"sinyal hanya punya {len(tps)} TP, butuh {cfg.TP_COUNT}"
            log.warning("SINYAL DILEWATI (%s): %s", sig.symbol, msg)
            self.hist.log_signal(sig, channel, "SKIPPED", msg)
            return

        log.info(
            "\n%s\nSINYAL   : %s %s   (channel: %s)\n"
            "Leverage : %sx  [sinyal asli %sx — DIABAIKAN]\n"
            "Entry    : %s\n"
            "           dipakai: %s\n"
            "TP dipakai: %s   [TP3+ dibuang: %s]\n"
            "SL       : %s  (%.2f%% dari entry)\n"
            "Equity   : %.2f | Sisa saldo: %.2f | Margin %.2f (%.0f%% dari sisa) | Notional %.2f\n"
            "Qty      : %.8f | Rugi bila SL kena: %.2f%% equity\n%s",
            "=" * 64,
            sig.side, sig.symbol, channel or "-",
            cfg.LEVERAGE, sig.raw_leverage,
            sig.entries, entry_note,
            tps, sig.take_profits[cfg.TP_COUNT:] or "tidak ada",
            sig.stop_loss, info["sl_percent"],
            info["equity"], info["available"], info["margin"], cfg.ENTRY_EQUITY_PERCENT, info["notional"],
            qty, info["risk_if_sl"],
            "=" * 64,
        )

        if cfg.DRY_RUN:
            log.info(">>> DRY_RUN aktif — tidak ada order yang dikirim.")
            self.hist.log_signal(sig, channel, "DRY_RUN", "", qty, cfg.LEVERAGE)
            return

        if cfg.REQUIRE_CONFIRM:
            # Di cloud (Railway/Render/Docker) tidak ada terminal interaktif.
            # input() akan melempar EOFError. Sinyal dilewati, bukan crash.
            if not sys.stdin or not sys.stdin.isatty():
                log.error("REQUIRE_CONFIRM aktif tapi tidak ada terminal "
                          "interaktif. Sinyal DILEWATI. Set REQUIRE_CONFIRM=false "
                          "kalau menjalankan di Railway/Docker.")
                self.hist.log_signal(sig, channel, "SKIPPED",
                                     "REQUIRE_CONFIRM tanpa terminal")
                return
            try:
                ans = input("Eksekusi sinyal ini? (ketik YA): ").strip()
            except EOFError:
                log.error("Tidak bisa membaca konfirmasi. Sinyal dilewati.")
                self.hist.log_signal(sig, channel, "SKIPPED", "konfirmasi gagal")
                return
            if ans != "YA":
                self.hist.log_signal(sig, channel, "CANCELLED", "dibatalkan user")
                return

        sid = self.hist.log_signal(sig, channel, "PLACING", "", qty, cfg.LEVERAGE)


        # [4] Leverage dipaksa ke cfg.LEVERAGE, tapi tidak boleh melebihi
        #     batas kontrak. Banyak altcoin di Innovation Zone Bybit hanya
        #     mengizinkan 5x-12.5x; set_leverage(10) akan ditolak di sana.
        lev = int(min(cfg.LEVERAGE, spec["max_leverage"]))
        if lev < cfg.LEVERAGE:
            log.warning("%s hanya mengizinkan %sx (diminta %sx). Memakai %sx — "
                        "ukuran posisi ikut mengecil.",
                        sig.symbol, spec["max_leverage"], cfg.LEVERAGE, lev)
            # Hitung ulang qty agar margin tetap 25% equity
            qty = qty * lev / cfg.LEVERAGE
        try:
            self.client.set_leverage(sig.symbol, lev)
        except Exception as e:
            log.error("Gagal set leverage: %s", e)
            self.hist.update_signal(sid, "ERROR", f"set_leverage: {e}")
            return

        # [3] Entry + TP1 + SL menempel pada order
        tag = uuid.uuid4().hex[:8]
        placed = 0
        for i, (price, share) in enumerate(zip(entries, splits), start=1):
            part = qty * share
            if float(self.client.round_qty(sig.symbol, part)) < float(spec["min_qty"]):
                log.warning("Entry %s dilewati: qty di bawah minimum kontrak", i)
                continue
            try:
                self.client.place_limit_entry(
                    symbol=sig.symbol,
                    side=sig.side,
                    qty=part,
                    price=price,
                    stop_loss=sig.stop_loss,
                    # CATATAN: TP TIDAK ditempelkan di order entry. Bybit tpslMode
                    # "Full" akan menutup 100% posisi saat TP kena, sehingga aturan
                    # TP1 50% / TP2 50% tidak akan jalan. TP dipasang sebagai order
                    # reduce-only terpisah begitu posisi terisi (lihat _place_tps).
                    order_link_id=f"e{i}-{tag}",
                )
                placed += 1
            except Exception as e:
                log.error("Gagal pasang entry %s: %s", i, e)

        if placed == 0:
            self.hist.update_signal(sid, "ERROR", "tidak ada entry yang terpasang")
            return

        self.hist.update_signal(sid, "PLACED", f"{placed} entry order, lev {lev}x")

        with self._lock:
            self.active[sig.symbol] = {
                "signal": sig,
                "entries_used": entries,
                "tps": tps,
                "sid": sid,
                "tag": tag,
                "max_size_seen": 0.0,
                "tp_placed": False,
                "sl_reset_done": False,
                "opened_at": time.time(),
            }

        threading.Thread(target=self._monitor, args=(sig.symbol,), daemon=True).start()

    # ------------------------------------------------------------ monitor
    def _monitor(self, symbol: str) -> None:
        log.info("Monitor dimulai: %s", symbol)
        idle = 0

        while True:
            time.sleep(cfg.POLL_SECONDS)
            with self._lock:
                st = self.active.get(symbol)
            if not st:
                return

            try:
                pos = self.client.position(symbol)
            except Exception as e:
                log.error("Monitor %s error: %s", symbol, e)
                continue

            sig: Signal = st["signal"]
            tps: list[float] = st["tps"]

            # ---- belum terisi / sudah tertutup -------------------------
            if not pos:
                if st["max_size_seen"] > 0:
                    log.info("Posisi %s tertutup. Membersihkan sisa order.", symbol)
                    self._cleanup(symbol, st, "CLOSED")
                    return
                idle += 1
                if idle * cfg.POLL_SECONDS > cfg.ENTRY_TIMEOUT_HOURS * 3600:
                    log.info("Entry %s tidak terisi, dibatalkan.", symbol)
                    self._cleanup(symbol, st, "EXPIRED")
                    return
                continue

            size = float(pos["size"])
            used = st.get("entries_used") or sig.entries
            entry_px = float(pos.get("avgPrice") or 0) or sum(used) / len(used)

            # ---- posisi bertambah → pasang ulang TP [6] ----------------
            if size > st["max_size_seen"] + 1e-12:
                st["max_size_seen"] = size
                self._place_tps(symbol, sig, tps, size, st["tag"])
                st["tp_placed"] = True

            # ---- TP1 kena → hapus SL, set ulang ke harga entry [7] -----
            elif (
                cfg.RESET_SL_TO_ENTRY
                and st["tp_placed"]
                and not st["sl_reset_done"]
                and size < st["max_size_seen"] * 0.995
            ):
                try:
                    self.client.remove_stop_loss(symbol)          # hapus SL lama
                    time.sleep(0.3)
                    self.client.set_stop_loss(symbol, entry_px)   # pasang di harga entry
                    st["sl_reset_done"] = True
                    log.info("TP1 %s kena → SL direset ke harga entry %.8f", symbol, entry_px)
                    self.hist.update_signal(st["sid"], "TP1_HIT", f"SL reset ke {entry_px}")
                except Exception as e:
                    log.error("Gagal reset SL ke entry: %s", e)

    # ------------------------------------------------- update SL susulan
    def apply_sl_update(self, upd, channel: str = "") -> None:
        """Terapkan pesan 'Set stoploss X' ke posisi yang sedang berjalan."""
        if not cfg.ALLOW_SL_UPDATE:
            return
        symbol = upd.symbol
        with self._lock:
            if symbol not in self.active:
                log.info("Update SL %s diabaikan: tidak ada posisi aktif", symbol)
                return
            st = self.active[symbol]
        if st.get("sl_reset_done"):
            log.info("Update SL %s diabaikan: SL sudah dikunci di breakeven", symbol)
            return
        if cfg.DRY_RUN:
            log.info("[DRY_RUN] Update SL %s -> %s", symbol, upd.stop_loss)
            return
        try:
            self.client.set_stop_loss(symbol, upd.stop_loss)
            log.info("SL %s diperbarui ke %s (dari %s)", symbol, upd.stop_loss, channel)
            self.hist.update_signal(st["sid"], "SL_UPDATED", f"SL -> {upd.stop_loss}")
        except Exception as e:
            log.error("Gagal update SL %s: %s", symbol, e)

    def _place_tps(self, symbol: str, sig: Signal, tps: list[float],
                   size: float, tag: str) -> None:
        """Pasang TP1 & TP2 reduce-only, 50% : 50%."""
        for o in self.client.open_orders(symbol):
            if o.get("reduceOnly"):
                try:
                    self.client.cancel_order(symbol, o["orderId"])
                except Exception:
                    pass

        shares = cfg.TP_SPLIT[: len(tps)]
        tot = sum(shares) or 1.0
        shares = [s / tot for s in shares]

        remaining = size
        for i, (tp, share) in enumerate(zip(tps, shares), start=1):
            q = size * share if i < len(tps) else remaining
            remaining -= q
            try:
                self.client.place_reduce_only_tp(
                    symbol=symbol, side=sig.side, qty=q, price=tp,
                    order_link_id=f"t{i}-{tag}",
                )
            except Exception as e:
                log.error("Gagal pasang TP%s: %s", i, e)
        log.info("TP1/TP2 terpasang untuk %s (size %.8f, 50/50)", symbol, size)

    def _cleanup(self, symbol: str, st: dict, status: str) -> None:
        try:
            self.client.cancel_all(symbol)
        except Exception:
            pass
        self.hist.update_signal(st["sid"], status, "")
        with self._lock:
            self.active.pop(symbol, None)
        try:
            self.hist.sync_from_bybit(self.client, lookback_days=2)
            self.check_drawdown()
        except CircuitBreaker:
            pass
        except Exception as e:
            log.error("Sync/drawdown check gagal: %s", e)
