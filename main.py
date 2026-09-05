"""
main.py — Titik masuk bot.

    python main.py            # jalankan bot
    python main.py --test     # uji parser saja, tanpa koneksi apa pun
    python main.py --sync     # tarik riwayat dari Bybit ke database lalu keluar
"""

from __future__ import annotations

import asyncio
import logging
import sys

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from bybit_client import BybitClient
from config import cfg
from history import History
from signal_parser import parse_signal, parse_sl_update
from trade_manager import CircuitBreaker, TradeManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler("bot.log", encoding="utf-8")],
)
log = logging.getLogger("main")

import samples as F

SAMPLES = [
    ("Global Crypto Research", F.GCR_SKR),
    ("Crypto World Updates", F.CWU_USELESS),
    ("Crypto World Updates", F.CWU_MARSCOIN),
    ("THE WOLF SCALPER", F.WOLF_TRIA),
]
def run_parser_test() -> None:
    print(f"\nLeverage dipaksa : {cfg.LEVERAGE}x")
    print(f"Margin per sinyal: {cfg.ENTRY_EQUITY_PERCENT}% equity")
    print(f"TP dipakai       : {cfg.TP_COUNT} pertama, split {cfg.TP_SPLIT}\n")
    for ch, msg in SAMPLES:
        s = parse_signal(msg)
        if not s:
            print(f"[{ch}] TIDAK TERBACA sebagai sinyal")
            continue
        avg = sum(s.entries) / len(s.entries)
        used = s.take_profits[: cfg.TP_COUNT]
        print(f"--- {ch} : {s.symbol} {s.side} ---")
        print(f"  Entry     : {s.entries}  (rata-rata {avg:.8f})")
        print(f"  TP dipakai: {used}   dibuang: {s.take_profits[cfg.TP_COUNT:] or '-'}")
        print(f"  SL        : {s.stop_loss}  "
              f"({abs(avg - s.stop_loss) / avg * 100:.2f}% dari entry)")
        print(f"  Lev sinyal: {s.raw_leverage}x -> dipaksa {cfg.LEVERAGE}x")
        print(f"  Valid     : {s.is_valid()}  ({s.why_invalid()})\n")


def run_sync() -> None:
    client = BybitClient(cfg.bybit_key, cfg.bybit_secret, testnet=cfg.testnet)
    hist = History(cfg.DB_PATH)
    n_e, n_p = hist.sync_from_bybit(client, lookback_days=7)
    print(f"Tersinkron: {n_e} eksekusi, {n_p} closed PnL -> {cfg.DB_PATH}")


async def amain() -> None:
    errs = cfg.validate()
    if errs:
        for e in errs:
            log.error("Konfigurasi: %s", e)
        sys.exit(1)

    log.info(
        "Mode %s | Testnet %s | Leverage %sx | Entry %s%% equity | "
        "TP 50/50 | Stop di -%s%% equity",
        "DRY RUN" if cfg.DRY_RUN else "LIVE TRADING",
        cfg.testnet, cfg.LEVERAGE, cfg.ENTRY_EQUITY_PERCENT, cfg.MAX_DRAWDOWN_PERCENT,
    )
    if not cfg.DRY_RUN and not cfg.testnet:
        log.warning("!!! UANG SUNGGUHAN !!!")
    if cfg.REQUIRE_CONFIRM and not (sys.stdin and sys.stdin.isatty()):
        log.error("REQUIRE_CONFIRM=true tapi tidak ada terminal interaktif "
                  "(Railway/Docker). Semua sinyal akan dilewati. "
                  "Set REQUIRE_CONFIRM=false di Variables.")
    if not cfg.session_string and not sys.stdin.isatty():
        log.error("TG_SESSION_STRING kosong dan tidak ada terminal untuk OTP. "
                  "Jalankan `python gen_session.py` di komputer sendiri dulu.")
        sys.exit(1)

    # Pemeriksaan pra-terbang otomatis. Di cloud, logs ini satu-satunya
    # umpan balik yang kamu punya sebelum bot mulai bekerja.
    try:
        from preflight import run_all
        probs, warns = run_all(check_telegram=False)
        if probs:
            log.error("PREFLIGHT GAGAL — bot tidak dijalankan:")
            for x in probs:
                log.error("  - %s", x)
            sys.exit(1)
        if warns:
            log.warning("Preflight lolos dengan %d peringatan (lihat di atas)", len(warns))
        else:
            log.info("Preflight lolos sepenuhnya.")
    except SystemExit:
        raise
    except Exception as e:
        log.warning("Preflight tidak bisa dijalankan (%s), lanjut tanpa cek.", e)

    client = BybitClient(cfg.bybit_key, cfg.bybit_secret, testnet=cfg.testnet)
    hist = History(cfg.DB_PATH)
    try:
        log.info("Equity USDT saat ini: %.2f", client.equity_usdt())
    except Exception as e:
        log.error("Gagal konek Bybit: %s", e)
        sys.exit(1)

    manager = TradeManager(client, hist)
    try:
        manager.check_drawdown()
    except CircuitBreaker:
        log.critical("Bot start dalam status HALTED. Reset dengan menghapus "
                     "baris meta 'start_equity' & 'halted_at' di %s", cfg.DB_PATH)

    if cfg.session_string:
        tg = TelegramClient(StringSession(cfg.session_string), cfg.api_id, cfg.api_hash)
        log.info("Login Telegram via TG_SESSION_STRING (mode cloud)")
    else:
        tg = TelegramClient(cfg.session_name, cfg.api_id, cfg.api_hash)
    await tg.start()

    # [1] Resolusi ketiga channel
    entities, names = [], {}
    for ch in cfg.channels:
        try:
            ent = await tg.get_entity(int(ch) if ch.lstrip("-").isdigit() else ch)
            entities.append(ent)
            names[ent.id] = getattr(ent, "title", str(ch))
            log.info("Channel OK: %s", names[ent.id])
        except Exception as e:
            log.error("Channel '%s' tidak bisa diakses: %s", ch, e)
    if len(entities) != len(cfg.channels):
        gagal = len(cfg.channels) - len(entities)
        log.error("%d dari %d channel TIDAK bisa diakses (lihat error di atas). "
                  "Bot tidak dijalankan agar kamu tidak mengira semua channel "
                  "terpantau padahal tidak. Perbaiki TG_CHANNELS lalu redeploy.",
                  gagal, len(cfg.channels))
        sys.exit(1)
    if not entities:
        log.error("Tidak ada channel valid.")
        sys.exit(1)
    log.info("FILTER AKTIF: hanya %d channel di atas yang dibaca. "
             "Grup lain yang kamu ikuti diabaikan total.", len(entities))

    @tg.on(events.NewMessage(chats=entities))
    async def handler(event):
        text = event.message.message or ""
        ch_name = names.get(event.chat_id, str(event.chat_id))

        # 1. Pesan susulan "Set stoploss X"
        upd = parse_sl_update(text)
        if upd:
            if not upd.symbol and event.message.reply_to:
                try:
                    q = await event.message.get_reply_message()
                    qs = parse_signal(q.message or "")
                    if qs:
                        upd.symbol = qs.symbol
                except Exception:
                    pass
            if upd.symbol:
                log.info("Update SL dari %s: %s -> %s", ch_name, upd.symbol, upd.stop_loss)
                await asyncio.to_thread(manager.apply_sl_update, upd, ch_name)
            return

        # 2. Sinyal entry baru
        sig = parse_signal(text)
        if not sig:
            return
        if not sig.is_valid():
            log.warning("Sinyal %s dari %s ditolak: %s",
                        sig.symbol, ch_name, sig.why_invalid())
            hist.log_signal(sig, ch_name, "INVALID", sig.why_invalid())
            return
        log.info("Sinyal terdeteksi dari %s: %s %s", ch_name, sig.side, sig.symbol)
        await asyncio.to_thread(manager.execute, sig, ch_name)

    log.info("Bot berjalan. Dashboard: python dashboard.py. Ctrl+C untuk berhenti.")
    await tg.run_until_disconnected()


if __name__ == "__main__":
    if "--test" in sys.argv:
        run_parser_test()
    elif "--sync" in sys.argv:
        run_sync()
    else:
        try:
            asyncio.run(amain())
        except KeyboardInterrupt:
            log.info("Bot dihentikan.")
