"""
tests/test_flow.py — Uji alur penuh dengan Bybit tiruan.

Skenario yang diuji:
  A. TP1 kena -> SL direset ke harga entry -> TP2 kena  (menang penuh)
  B. TP1 kena -> harga balik -> kena SL breakeven       (menang kecil)
  C. Kena SL sebelum TP1                                 (kalah)
  D. Leverage sinyal 50x tetap dipaksa jadi 10x
  E. Margin yang dipakai benar-benar 25% equity
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.update({
    "TG_API_ID": "1", "TG_API_HASH": "x", "TG_CHANNELS": "@a",
    "BYBIT_API_KEY": "k", "BYBIT_API_SECRET": "s",
    "DRY_RUN": "false", "BYBIT_TESTNET": "true",
    "ENTRY_EQUITY_PERCENT": "25", "LEVERAGE": "10",
    "POLL_SECONDS": "1", "DB_PATH": "/tmp/test_flow.db",
    "MAX_CONCURRENT_POSITIONS": "1", "ENTRY_MODE": "nearest",
})

for f in ("/tmp/test_flow.db",):
    if os.path.exists(f):
        os.remove(f)

from bybit_client import BybitClient          # noqa: E402
from config import cfg                        # noqa: E402
from history import History                   # noqa: E402
from signal_parser import parse_signal        # noqa: E402
from trade_manager import TradeManager        # noqa: E402
from mock_bybit import MockSession            # noqa: E402
import samples as F                           # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {detail}")


class FakeClient(BybitClient):
    def __init__(self, session):
        self.session = session
        self.testnet = True
        self._instrument_cache = {}


def build(equity=20.0, db="/tmp/test_flow.db", max_leverage="50"):
    if os.path.exists(db):
        os.remove(db)
    ms = MockSession(equity=equity, max_leverage=max_leverage)
    cl = FakeClient(ms)
    h = History(db)
    tm = TradeManager(cl, h)
    return ms, cl, h, tm


def drive(ms, symbol, prices, pause=1.2):
    """Gerakkan harga langkah demi langkah, beri waktu monitor bekerja."""
    for p in prices:
        ms.set_price(symbol, p)
        time.sleep(pause)


# =========================================================================
print("\n" + "=" * 70)
print("SKENARIO A — #USELESS: entry terisi, TP1 kena, SL->entry, TP2 kena")
print("=" * 70)

ms, cl, h, tm = build(equity=20.0)
sig = parse_signal(F.CWU_USELESS)
ms.set_price("USELESSUSDT", 0.2360)          # di atas entry, order belum kena
start_eq = ms.equity
tm.execute(sig, "@CryptoWorldUpdates")
time.sleep(1.0)

# Cek ukuran & leverage sebelum harga bergerak
entry_orders = [o for o in ms.orders if not o["reduceOnly"]]
total_qty = sum(o["qty"] for o in entry_orders)
avg_entry = entry_orders[0]["price"]
notional = total_qty * avg_entry
check("leverage dipaksa 10x", ms.leverage.get("USELESSUSDT") == 10,
      ms.leverage.get("USELESSUSDT"))
check("HANYA 1 order entry (mode nearest)", len(entry_orders) == 1, len(entry_orders))
check("entry dipilih yang terdekat harga 0.2360 -> 0.2330",
      entry_orders and abs(entry_orders[0]["price"] - 0.2330) < 1e-9,
      entry_orders[0]["price"] if entry_orders else None)
check(f"margin = 25% equity (notional {notional:.2f} ~ 2.5x equity)",
      abs(notional / start_eq - 2.5) < 0.05, f"{notional/start_eq:.3f}x")
check("SL menempel pada order entry",
      all(o["stopLoss"] == sig.stop_loss for o in entry_orders))
check("TP TIDAK menempel di order entry (bug lama sudah diperbaiki)",
      all("takeProfit" not in o for o in entry_orders))

# Harga turun -> kedua entry terisi
drive(ms, "USELESSUSDT", [0.2330])
pos = ms.positions.get("USELESSUSDT")
check("entry rata-rata = harga entry terpilih",
      pos and abs(pos["avgPrice"] - 0.2330) < 1e-9, pos["avgPrice"] if pos else None)
check("posisi terbuka", pos is not None and pos["size"] > 0)
size_before = float(pos["size"])   # snapshot: dict di-mutasi oleh mock

tp_orders = [o for o in ms.orders if o["reduceOnly"] and o["status"] == "New"]
check("2 order TP reduce-only dipasang", len(tp_orders) == 2, len(tp_orders))
if len(tp_orders) == 2:
    check("TP split 50/50", abs(tp_orders[0]["qty"] - tp_orders[1]["qty"]) < 1,
          [o["qty"] for o in tp_orders])
    check("TP3 (0.2722) diabaikan",
          all(abs(o["price"] - 0.2722) > 1e-9 for o in tp_orders),
          [o["price"] for o in tp_orders])

entry_px = pos["avgPrice"]
drive(ms, "USELESSUSDT", [0.2400])           # TP1
time.sleep(1.5)
pos2 = ms.positions.get("USELESSUSDT")
check("TP1 menutup 50% posisi",
      pos2 and abs(float(pos2["size"]) / size_before - 0.5) < 0.02,
      f"{float(pos2['size'])}/{size_before}" if pos2 else None)
check("SL direset ke harga entry", pos2 and abs(pos2["stopLoss"] - entry_px) < 1e-6,
      f"SL={pos2['stopLoss'] if pos2 else None} entry={entry_px}")

drive(ms, "USELESSUSDT", [0.2500])           # TP2
time.sleep(1.5)
check("posisi tertutup penuh setelah TP2", "USELESSUSDT" not in ms.positions)
pnl_a = ms.equity - start_eq
print(f"\n  Equity: {start_eq:.4f} -> {ms.equity:.4f}   PnL = {pnl_a:+.4f} USDT "
      f"({pnl_a/start_eq*100:+.2f}%)")
check("skenario A untung", pnl_a > 0, pnl_a)

# =========================================================================
print("\n" + "=" * 70)
print("SKENARIO B — #SKR: TP1 kena, harga balik, kena SL breakeven")
print("=" * 70)

ms, cl, h, tm = build(equity=20.0, db="/tmp/test_flow_b.db")
sig = parse_signal(F.GCR_SKR)
ms.set_price("SKRUSDT", 0.0245)
start_eq = ms.equity
tm.execute(sig, "@GlobalCryptoResearch")
time.sleep(1.0)
drive(ms, "SKRUSDT", [0.0238])               # entry terdekat terisi
pos = ms.positions.get("SKRUSDT")
entry_px = pos["avgPrice"] if pos else 0
drive(ms, "SKRUSDT", [0.0250])               # TP1 kena
time.sleep(1.5)
pos2 = ms.positions.get("SKRUSDT")
check("SL dikunci di entry setelah TP1",
      pos2 and abs(pos2["stopLoss"] - entry_px) < 1e-7,
      f"{pos2['stopLoss'] if pos2 else None} vs {entry_px}")
drive(ms, "SKRUSDT", [entry_px * 0.999])     # balik ke breakeven
time.sleep(1.2)
check("sisa posisi ditutup di breakeven", "SKRUSDT" not in ms.positions)
pnl_b = ms.equity - start_eq
print(f"\n  Equity: {start_eq:.4f} -> {ms.equity:.4f}   PnL = {pnl_b:+.4f} USDT "
      f"({pnl_b/start_eq*100:+.2f}%)")
check("skenario B tetap untung kecil (TP1 mengunci profit)", pnl_b > 0, pnl_b)

# =========================================================================
print("\n" + "=" * 70)
print("SKENARIO C — $TRIA: kena SL sebelum TP1 (kalah)")
print("=" * 70)

ms, cl, h, tm = build(equity=20.0, db="/tmp/test_flow_c.db")
sig = parse_signal(F.WOLF_TRIA)
ms.set_price("TRIAUSDT", 0.005450)
start_eq = ms.equity
tm.execute(sig, "@TheWolfCrypto")
time.sleep(1.0)
check("leverage 50x di sinyal -> dipaksa 10x", ms.leverage.get("TRIAUSDT") == 10,
      ms.leverage.get("TRIAUSDT"))
drive(ms, "TRIAUSDT", [0.005400])            # entry terdekat terisi
drive(ms, "TRIAUSDT", [0.005120])            # SL kena
time.sleep(1.5)
check("posisi tertutup oleh SL", "TRIAUSDT" not in ms.positions)
open_left = [o for o in ms.orders if o["status"] == "New"]
check("order TP sisa dibatalkan", len(open_left) == 0, open_left)
pnl_c = ms.equity - start_eq
print(f"\n  Equity: {start_eq:.4f} -> {ms.equity:.4f}   PnL = {pnl_c:+.4f} USDT "
      f"({pnl_c/start_eq*100:+.2f}%)")
check("skenario C rugi (sesuai harapan)", pnl_c < 0, pnl_c)

# =========================================================================
print("\n" + "=" * 70)
print("SKENARIO D — riwayat tersinkron ke database")
print("=" * 70)
n_e, n_p = h.sync_from_bybit(cl, lookback_days=1)
st = h.stats()
print(f"  Eksekusi tersimpan: {n_e} | Closed PnL: {n_p}")
print(f"  Statistik: {st}")
check("eksekusi buy/sell tercatat", n_e > 0, n_e)
check("closed PnL tercatat", n_p > 0, n_p)
check("total PnL cocok dg simulasi", abs(st["total_pnl"] - pnl_c) < 0.02,
      f"{st['total_pnl']} vs {pnl_c}")

# =========================================================================
print("\n" + "=" * 70)
print("SKENARIO E — circuit breaker berhenti di -50%")
print("=" * 70)
ms, cl, h, tm = build(equity=20.0, db="/tmp/test_flow_e.db")
check("awal tidak halted", not tm.halted)
ms.equity = 9.0        # turun 55%
try:
    tm.check_drawdown()
    check("circuit breaker terpicu", False, "tidak terpicu")
except Exception:
    check("circuit breaker terpicu", tm.halted)
sig = parse_signal(F.CWU_MARSCOIN)
ms.set_price("MARSCOINUSDT", 0.0890)
tm.execute(sig, "@test")
check("sinyal baru ditolak saat halted",
      not any(not o["reduceOnly"] for o in ms.orders), ms.orders)

# =========================================================================
print("\n" + "=" * 70)
print("SKENARIO F — pemilihan entry terdekat pada berbagai harga pasar")
print("=" * 70)
sig = parse_signal(F.GCR_SKR)   # entries 0.0238 / 0.0228
for market, expect, note in [
    (0.0245, 0.0238, "harga di ATAS kedua entry"),
    (0.0236, 0.0238, "harga sedikit di bawah entry-1"),
    (0.0234, 0.0238, "harga di tengah, lebih dekat entry-1"),
    (0.0232, 0.0228, "harga di tengah, lebih dekat entry-2"),
    (0.0220, 0.0228, "harga di BAWAH kedua entry"),
]:
    ms2, cl2, h2, tm2 = build(equity=20.0, db="/tmp/test_flow_f.db")
    ms2.set_price("SKRUSDT", market)
    chosen, splits, note2 = tm2.select_entries(sig)
    check(f"pasar {market} -> pilih {expect}  ({note})",
          len(chosen) == 1 and abs(chosen[0] - expect) < 1e-9, chosen)
    check(f"  ukuran penuh 100% (bukan dibagi)", splits == [1.0], splits)

# =========================================================================
print("\n" + "=" * 70)
print("SKENARIO G — kontrak Innovation Zone dengan leverage maks di bawah 10x")
print("=" * 70)
ms, cl, h, tm = build(equity=20.0, db="/tmp/test_flow_g.db", max_leverage="5")
sig = parse_signal(F.CWU_USELESS)
ms.set_price("USELESSUSDT", 0.2360)
tm.execute(sig, "@test")
time.sleep(1.0)
check("leverage diturunkan ke batas kontrak (5x), tidak error",
      ms.leverage.get("USELESSUSDT") == 5, ms.leverage.get("USELESSUSDT"))
eo = [o for o in ms.orders if not o["reduceOnly"]]
check("order tetap terpasang", len(eo) == 1, len(eo))
if eo:
    notional = eo[0]["qty"] * eo[0]["price"]
    check(f"notional menyesuaikan 5x (margin tetap 25% = $5, notional ~$25)",
          abs(notional - 25.0) < 1.0, f"${notional:.2f}")

print("\n" + "=" * 70)
print(f"HASIL AKHIR: {PASS} pass, {FAIL} fail")
print("=" * 70)
sys.exit(1 if FAIL else 0)
