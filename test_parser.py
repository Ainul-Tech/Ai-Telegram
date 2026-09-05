"""Uji parser terhadap pesan asli dari 3 channel."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from signal_parser import parse_signal, parse_sl_update
import samples as F

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")

print("\n=== 1. Global Crypto Research (#SKR) ===")
s = parse_signal(F.GCR_SKR)
print(f"  -> {s.symbol} {s.side} entries={s.entries} tp={s.take_profits} sl={s.stop_loss} lev={s.raw_leverage}")
check("symbol SKRUSDT", s.symbol == "SKRUSDT", s.symbol)
check("side Buy", s.side == "Buy")
check("entries [0.0238, 0.0228]", s.entries == [0.0238, 0.0228], s.entries)
check("tp [0.025, 0.0265, 0.0288]", s.take_profits == [0.025, 0.0265, 0.0288], s.take_profits)
check("sl 0.0218", s.stop_loss == 0.0218, s.stop_loss)
check("valid", s.is_valid(), s.why_invalid())

print("\n=== 2. Crypto World Updates (#USELESS) ===")
s = parse_signal(F.CWU_USELESS)
print(f"  -> {s.symbol} {s.side} entries={s.entries} tp={s.take_profits} sl={s.stop_loss} lev={s.raw_leverage}")
check("symbol USELESSUSDT", s.symbol == "USELESSUSDT", s.symbol)
check("entries [0.233, 0.225]", s.entries == [0.233, 0.225], s.entries)
check("tp [0.24, 0.25, 0.2722]", s.take_profits == [0.24, 0.25, 0.2722], s.take_profits)
check("sl 0.218", s.stop_loss == 0.218, s.stop_loss)
check("lev sinyal 50", s.raw_leverage == 50, s.raw_leverage)
check("valid", s.is_valid(), s.why_invalid())

print("\n=== 3. Crypto World Updates (#MARSCOIN) ===")
s = parse_signal(F.CWU_MARSCOIN)
print(f"  -> {s.symbol} {s.side} entries={s.entries} tp={s.take_profits} sl={s.stop_loss}")
check("entries [0.0885, 0.086]", s.entries == [0.0885, 0.086], s.entries)
check("tp [0.0925, 0.099, 0.105]", s.take_profits == [0.0925, 0.099, 0.105], s.take_profits)
check("valid", s.is_valid(), s.why_invalid())

print("\n=== 4. THE WOLF SCALPER ($TRIA) ===")
s = parse_signal(F.WOLF_TRIA)
print(f"  -> {s.symbol} {s.side} entries={s.entries} tp={s.take_profits} sl={s.stop_loss} lev={s.raw_leverage}")
check("symbol TRIAUSDT", s.symbol == "TRIAUSDT", s.symbol)
check("entries [0.0054, 0.00534]", s.entries == [0.0054, 0.00534], s.entries)
check("tp dash-separated", s.take_profits == [0.00553, 0.00578, 0.0062], s.take_profits)
check("sl 0.00512", s.stop_loss == 0.00512, s.stop_loss)
check("valid", s.is_valid(), s.why_invalid())

print("\n=== 5. Pesan yang BUKAN sinyal (harus None) ===")
for name, msg in [("hit stoploss", F.NOT_SIGNAL_HIT_SL),
                  ("promo DM", F.NOT_SIGNAL_PROMO),
                  ("all targets achieved", F.NOT_SIGNAL_ACHIEVED),
                  ("recovery tanpa SL", F.NOT_SIGNAL_RECOVERY)]:
    r = parse_signal(msg)
    check(f"{name} -> None", r is None or not r.is_valid(), repr(r))

print("\n=== 6. Update stop loss ===")
u = parse_sl_update(F.SL_UPDATE)
print(f"  -> {u}")
check("SL update terbaca", u is not None and u.stop_loss == 0.0214, u)
check("symbol SKRUSDT", u and u.symbol == "SKRUSDT", u.symbol if u else None)
check("sinyal biasa bukan SL update", parse_sl_update(F.GCR_SKR) is None)

print("\n=== 7. Validasi sinyal cacat ===")
b = parse_signal(F.BAD_SL_WRONG_SIDE)
check("LONG dg SL di atas entry ditolak", b is not None and not b.is_valid(), b.why_invalid() if b else None)
sh = parse_signal(F.BAD_SHORT_OK)
print(f"  -> SHORT: {sh.symbol} entries={sh.entries} tp={sh.take_profits} sl={sh.stop_loss}")
check("SHORT valid dikenali", sh is not None and sh.is_valid() and sh.side == "Sell",
      sh.why_invalid() if sh else None)

print(f"\n{'='*50}\nHASIL: {PASS} pass, {FAIL} fail\n{'='*50}")
sys.exit(1 if FAIL else 0)
