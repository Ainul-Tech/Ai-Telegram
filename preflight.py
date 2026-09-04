"""
preflight.py — Pemeriksaan kesiapan SEBELUM trial. Hanya membaca, tidak
pernah mengirim order.

    python preflight.py

Memeriksa: dependensi, konfigurasi, koneksi Bybit, jenis akun, mode posisi,
izin API key, ketersediaan symbol, dan koneksi Telegram.
"""

from __future__ import annotations

import asyncio
import sys

OK, WARN, ERR = "  [OK]  ", "  [!!]  ", "  [XX]  "
problems: list[str] = []
warnings: list[str] = []


def ok(msg):
    print(OK + msg)


def warn(msg):
    print(WARN + msg)
    warnings.append(msg)


def err(msg):
    print(ERR + msg)
    problems.append(msg)


def run_all(check_telegram: bool = True, exit_on_fail: bool = False):
    """Jalankan semua pemeriksaan. Kembalikan (problems, warnings)."""
    problems.clear()
    warnings.clear()

    print("=" * 68)
    print("PEMERIKSAAN PRA-TRIAL")
    print("=" * 68)

    # --- 1. Dependensi -------------------------------------------------------
    print("\n[1] Dependensi")
    for mod, pkg in [("pybit", "pybit"), ("telethon", "telethon"),
                     ("dotenv", "python-dotenv"), ("flask", "flask")]:
        try:
            __import__(mod)
            ok(f"{pkg} terpasang")
        except ImportError:
            err(f"{pkg} BELUM terpasang -> pip install -r requirements.txt")

    if problems:
        print("\nHentikan dulu: pasang dependensi lalu jalankan ulang.")
        return problems, warnings

    from config import cfg  # noqa: E402

    # --- 2. Konfigurasi ------------------------------------------------------
    print("\n[2] Konfigurasi .env")
    errs = cfg.validate()
    for e in errs:
        err(e)
    if not errs:
        ok("semua field wajib terisi")

    print(f"         Leverage         : {cfg.LEVERAGE}x")
    print(f"         Margin per sinyal: {cfg.ENTRY_EQUITY_PERCENT}% equity "
          f"(notional {cfg.ENTRY_EQUITY_PERCENT * cfg.LEVERAGE / 100:.1f}x equity)")
    print(f"         Mode entry       : {cfg.ENTRY_MODE}")
    print(f"         TP               : {cfg.TP_COUNT} pertama, split {cfg.TP_SPLIT}")
    print(f"         SL->entry stlh TP1: {cfg.RESET_SL_TO_ENTRY}")
    print(f"         Stop di drawdown : {cfg.MAX_DRAWDOWN_PERCENT}%")
    print(f"         Posisi bersamaan : {cfg.MAX_CONCURRENT_POSITIONS}")

    if cfg.DRY_RUN:
        ok("DRY_RUN aktif — bot TIDAK akan mengirim order (aman untuk mulai)")
    else:
        if cfg.testnet:
            warn("DRY_RUN mati + TESTNET — order sungguhan di testnet (uang mainan)")
        else:
            err("DRY_RUN mati + LIVE — bot akan memakai UANG SUNGGUHAN")

    # --- 3. Koneksi Bybit ----------------------------------------------------
    print(f"\n[3] Koneksi Bybit ({'TESTNET' if cfg.testnet else 'LIVE / MAINNET'})")
    client = None
    equity = 0.0
    try:
        from bybit_client import BybitClient
        client = BybitClient(cfg.bybit_key, cfg.bybit_secret, testnet=cfg.testnet)
        equity = client.equity_usdt()
        ok(f"tersambung. Equity USDT = {equity:.4f}")
        if equity <= 0:
            err("equity 0 — dana belum masuk, atau salah pilih testnet/mainnet")
        elif equity < 10:
            warn(f"equity hanya {equity:.2f} USDT — minimum order Bybit mungkin tak terpenuhi")
    except Exception as e:
        err(f"gagal konek: {e}")

    # --- 4. Izin API key -----------------------------------------------------
    if client:
        print("\n[4] Izin API key")
        try:
            info = client.session.get_api_key_information().get("result", {})
            perms = info.get("permissions", {})
            contract = perms.get("ContractTrade", [])
            wallet = perms.get("Wallet", [])
            if contract:
                ok(f"Contract Trade aktif: {contract}")
            else:
                err("izin Contract Trade TIDAK aktif — bot tak bisa order")
            if any("Withdraw" in str(w) for w in wallet):
                err("izin WITHDRAW aktif — MATIKAN sekarang, sangat berbahaya")
            else:
                ok("izin Withdraw tidak aktif (benar)")
            ips = info.get("ips", [])
            if ips and ips != ["*"]:
                ok(f"dibatasi ke IP: {ips}")
            else:
                warn("API key tidak dibatasi IP — sebaiknya dikunci ke IP server")
        except Exception as e:
            warn(f"tidak bisa membaca info API key: {e}")

    # --- 5. Jenis akun & mode posisi ----------------------------------------
    if client:
        print("\n[5] Jenis akun & mode posisi")
        try:
            acc = client.session.get_account_info().get("result", {})
            status = acc.get("unifiedMarginStatus")
            if status in (3, 4, 5, 6):
                ok(f"akun UNIFIED (status {status}) — sesuai kebutuhan bot")
            else:
                warn(f"unifiedMarginStatus={status}. Bot memakai accountType='UNIFIED'. "
                     "Kalau equity terbaca 0, upgrade akun ke Unified Trading Account.")
        except Exception as e:
            warn(f"tidak bisa membaca info akun: {e}")

        try:
            r = client.session.get_positions(category="linear", settleCoin="USDT")
            lst = r.get("result", {}).get("list", [])
            idxs = {int(p.get("positionIdx", 0)) for p in lst}
            if idxs and idxs != {0}:
                err(f"akun dalam HEDGE MODE (positionIdx {idxs}). Bot memakai "
                    "positionIdx=0 (one-way). Ubah ke One-Way Mode di Bybit.")
            else:
                ok("mode posisi one-way (positionIdx=0) — sesuai")
            openpos = [p for p in lst if float(p.get("size", 0)) > 0]
            if openpos:
                warn(f"ada {len(openpos)} posisi terbuka: "
                     f"{[p['symbol'] for p in openpos]}. Bot bisa bentrok — "
                     "sebaiknya tutup dulu sebelum trial.")
            else:
                ok("tidak ada posisi terbuka")
        except Exception as e:
            warn(f"tidak bisa membaca posisi: {e}")

    # --- 6. Ketersediaan symbol ---------------------------------------------
    if client:
        print("\n[6] Ketersediaan symbol di Bybit linear perpetual")
        print("        (symbol dari 4 sinyal contoh kamu)")
        for sym in ["SKRUSDT", "USELESSUSDT", "MARSCOINUSDT", "TRIAUSDT",
                    "BTCUSDT", "ETHUSDT"]:
            try:
                spec = client.instrument(sym)
                if spec and spec.get("status") == "Trading":
                    minq = float(spec["min_qty"])
                    notional = equity * cfg.ENTRY_EQUITY_PERCENT / 100 * cfg.LEVERAGE
                    px = client.last_price(sym)
                    qty = notional / px if px else 0
                    fit = "cukup" if qty >= minq else f"TERLALU KECIL (min {minq})"
                    ok(f"{sym:<14} tersedia | harga {px} | qty kamu {qty:.4f} -> {fit}")
                else:
                    warn(f"{sym:<14} TIDAK tersedia / tidak trading di Bybit")
            except Exception:
                warn(f"{sym:<14} TIDAK tersedia di Bybit linear")

    # --- 7. Telegram ---------------------------------------------------------
    if not check_telegram:
        return problems, warnings
    print("\n[7] Koneksi Telegram")


    async def check_tg():
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            if cfg.session_string:
                tg = TelegramClient(StringSession(cfg.session_string),
                                    cfg.api_id, cfg.api_hash)
            else:
                tg = TelegramClient(cfg.session_name, cfg.api_id, cfg.api_hash)
            await tg.start()
            me = await tg.get_me()
            ok(f"login sebagai {me.first_name} (@{me.username or '-'})")
            for ch in cfg.channels:
                try:
                    ent = await tg.get_entity(int(ch) if ch.lstrip("-").isdigit() else ch)
                    ok(f"channel terbaca: {getattr(ent, 'title', ch)}")
                except Exception as e:
                    err(f"channel '{ch}' TIDAK bisa diakses: {e}")
            await tg.disconnect()
        except Exception as e:
            err(f"gagal konek Telegram: {e}")


    try:
        asyncio.run(check_tg())
    except Exception as e:
        err(f"Telegram: {e}")

    # --- Ringkasan -----------------------------------------------------------
    print("\n" + "=" * 68)
    if problems:
        print(f"BELUM SIAP — {len(problems)} masalah harus diperbaiki:")
        for p in problems:
            print(f"  - {p}")
    elif warnings:
        print(f"SIAP DENGAN CATATAN — {len(warnings)} peringatan:")
        for w in warnings:
            print(f"  - {w}")
        print("\nBoleh mulai, tapi baca peringatan di atas dulu.")
    else:
        print("SEMUA PEMERIKSAAN LULUS. Siap dijalankan.")
    print("=" * 68)
    return problems, warnings


if __name__ == "__main__":
    p, w = run_all(check_telegram=True)
    sys.exit(1 if p else 0)
