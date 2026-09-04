"""
config.py — Semua pengaturan dibaca dari environment variable (.env).
Jangan pernah menaruh API key langsung di dalam kode.
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "y", "on")


def _float(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v not in (None, "") else default


def _int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v not in (None, "") else default


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


@dataclass
class Config:
    # ----------------------------------------------------------- Telegram --
    api_id: int = _int("TG_API_ID", 0)
    api_hash: str = os.getenv("TG_API_HASH", "")
    session_name: str = os.getenv("TG_SESSION", "signal_session")
    # Untuk deploy di cloud: hasil dari `python gen_session.py`
    session_string: str = os.getenv("TG_SESSION_STRING", "")
    # [1] Tiga sumber channel. @username atau ID numerik (-100xxxxxxxxxx)
    channels: list[str] = field(default_factory=lambda: _list("TG_CHANNELS"))

    # -------------------------------------------------------------- Bybit --
    bybit_key: str = os.getenv("BYBIT_API_KEY", "")
    bybit_secret: str = os.getenv("BYBIT_API_SECRET", "")
    testnet: bool = _bool("BYBIT_TESTNET", True)

    # ------------------------------------------------------------ Trading --
    # [4] Leverage SELALU 10x, angka dari sinyal diabaikan total
    LEVERAGE: int = _int("LEVERAGE", 10)

    # [1] Margin per sinyal = 25% dari equity
    #     Notional = 25% x 10x = 250% equity per posisi
    ENTRY_EQUITY_PERCENT: float = _float("ENTRY_EQUITY_PERCENT", 25.0)

    # Entry persis sesuai harga sinyal, tanpa filter slippage.
    # ENTRY_MODE menentukan entry mana yang dipakai bila sinyal punya >1 entry:
    #   "nearest" - HANYA SATU order, di entry yang paling dekat harga pasar
    #               saat sinyal masuk. Ukuran penuh. (default, paling aman)
    #   "first"   - hanya entry pertama yang tertulis di sinyal
    #   "split"   - semua entry, dibagi rata (perilaku lama)
    ENTRY_MODE: str = os.getenv("ENTRY_MODE", "nearest").strip().lower()
    ENTRY_SPLIT: list[float] = field(default_factory=lambda: [0.5, 0.5])

    # [6] Hanya TP1 dan TP2 dipakai, masing-masing 50%. TP3/TP4 diabaikan.
    TP_COUNT: int = _int("TP_COUNT", 2)
    TP_SPLIT: list[float] = field(default_factory=lambda: [0.5, 0.5])

    # [7] Setelah TP1 kena, SL lama dihapus dan diset ulang = harga entry
    RESET_SL_TO_ENTRY: bool = _bool("RESET_SL_TO_ENTRY", True)

    # [8] Circuit breaker: bot berhenti kalau equity turun lebih dari 50%
    MAX_DRAWDOWN_PERCENT: float = _float("MAX_DRAWDOWN_PERCENT", 50.0)

    # Opsional: tolak sinyal yang SL-nya terlalu jauh dari entry. 0 = mati.
    MAX_SL_PERCENT: float = _float("MAX_SL_PERCENT", 0.0)

    # Terima pesan susulan "Set stoploss X" untuk posisi berjalan
    ALLOW_SL_UPDATE: bool = _bool("ALLOW_SL_UPDATE", True)

    # ---------------------------------------------------------- Keamanan ---
    DRY_RUN: bool = _bool("DRY_RUN", True)
    REQUIRE_CONFIRM: bool = _bool("REQUIRE_CONFIRM", False)
    SYMBOL_WHITELIST: list[str] = field(
        default_factory=lambda: [s.upper() for s in _list("SYMBOL_WHITELIST")]
    )
    # Dengan notional 100% equity per posisi, lebih dari 1 posisi = eksposur >100%
    MAX_CONCURRENT_POSITIONS: int = _int("MAX_CONCURRENT_POSITIONS", 1)
    POLL_SECONDS: int = _int("POLL_SECONDS", 5)
    ENTRY_TIMEOUT_HOURS: float = _float("ENTRY_TIMEOUT_HOURS", 12.0)

    # --------------------------------------------------------- Dashboard ---
    # Di Railway, pasang Volume dengan mount path /data lalu set DB_PATH=/data/trades.db
    # supaya riwayat & patokan circuit breaker tidak hilang saat redeploy.
    DB_PATH: str = os.getenv("DB_PATH", "trades.db")
    DASH_HOST: str = os.getenv("DASH_HOST", "127.0.0.1")
    # Railway/Render menyuntikkan PORT otomatis
    DASH_PORT: int = _int("PORT", _int("DASH_PORT", 8080))

    def validate(self) -> list[str]:
        errs = []
        if not self.api_id or not self.api_hash:
            errs.append("TG_API_ID / TG_API_HASH belum diisi")
        if not self.channels:
            errs.append("TG_CHANNELS belum diisi")
        if not self.bybit_key or not self.bybit_secret:
            errs.append("BYBIT_API_KEY / BYBIT_API_SECRET belum diisi")
        if self.ENTRY_MODE not in ("nearest", "first", "split"):
            errs.append("ENTRY_MODE harus nearest / first / split")
        if not (1 <= self.LEVERAGE <= 25):
            errs.append("LEVERAGE di luar batas aman (1-25)")
        if not (0 < self.ENTRY_EQUITY_PERCENT <= 50):
            errs.append("ENTRY_EQUITY_PERCENT harus 0-50")
        if not self.api_hash and not self.session_string:
            errs.append("perlu TG_API_HASH atau TG_SESSION_STRING")
        return errs


cfg = Config()
