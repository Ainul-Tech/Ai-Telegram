"""
signal_parser.py — Parser multi-format untuk sinyal Telegram.

Format yang didukung (diuji dengan pesan asli dari 3 channel):

A. Global Crypto Research
     Coin #SKR/USDT
     Position: LONG
     Leverage:  Cross 10x To 50x
     Entries:  0.0238 - 0.0228
     Targets: 0.0250, 0.0265, 0.0288
     Stop Loss: 0.0218

B. Crypto World Updates
     #USELESS/USDT
     SIGNAL Type: Regular (LONG)
     Entry Targets:
     1) 0.2330
     2) 0.2250
     Take-Profit Targets
     1) 0.2400
     ...
     Stop Target:
     1) 0.2180

C. THE WOLF SCALPER
     LONG: $TRIA/USDT
     LEVERAGE: 50x
     ENTRY PRICE: 0.005400
     2nd ENTRY: 0.005340
     TAKE- PROFIT
     0.005530-0.005780-0.006200
     STOP LOSS: 0.005120

Selain sinyal baru, parser juga mengenali pesan susulan:
  - "Set stoploss 0.0214"   -> SLUpdate (ubah SL posisi berjalan)
  - "#SKR hit stoploss"     -> diabaikan (informasi saja)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NUM = r"\d+\.\d+|\d+"

# Header yang membuka sebuah bagian
_H_ENTRY = re.compile(
    r"^\W*(?:\d+(?:st|nd|rd|th)\s+)?(entry\s+targets?|entries|entry\s+price|entry)\b\s*:?",
    re.IGNORECASE,
)
_H_TP = re.compile(
    r"^\W*(take\s*-?\s*profit\s+targets?|take\s*-?\s*profit|targets?|tp)\b\s*:?",
    re.IGNORECASE,
)
_H_SL = re.compile(
    r"^\W*(stop\s+targets?|stop\s*-?\s*loss|stoploss|sl)\b\s*:?",
    re.IGNORECASE,
)
# Baris yang MENGHENTIKAN pengumpulan angka (bukan bagian dari entry/TP/SL)
_H_STOPWORD = re.compile(
    r"^\W*(leverage|amount|position|signal\s*type|coin|published|trailing|"
    r"no\s*reacts|reactions|click|join|http|www\.|risk|margin|note|"
    r"#we_stand|use\s+\d+%|capital)\b",
    re.IGNORECASE,
)

_RE_SYMBOL = re.compile(
    r"[#$]?\s*([A-Z][A-Z0-9]{1,14})\s*/\s*(USDT|USDC|USD)\b", re.IGNORECASE
)
_RE_SET_SL = re.compile(
    r"\b(?:set|update|move|new)\s+stop\s*-?\s*loss\s*(?:to|at|:)?\s*(" + NUM + r")",
    re.IGNORECASE,
)


@dataclass
class Signal:
    symbol: str
    side: str                      # "Buy" (LONG) / "Sell" (SHORT)
    entries: list[float] = field(default_factory=list)
    take_profits: list[float] = field(default_factory=list)
    stop_loss: float | None = None
    raw_leverage: int | None = None
    raw_text: str = ""

    def is_valid(self) -> bool:
        if not self.symbol or not self.entries or not self.take_profits:
            return False
        if self.stop_loss is None or self.stop_loss <= 0:
            return False
        if any(e <= 0 for e in self.entries) or any(t <= 0 for t in self.take_profits):
            return False

        if self.side == "Buy":
            if self.stop_loss >= min(self.entries):
                return False
            if any(tp <= max(self.entries) for tp in self.take_profits):
                return False
        else:
            if self.stop_loss <= max(self.entries):
                return False
            if any(tp >= min(self.entries) for tp in self.take_profits):
                return False
        return True

    def why_invalid(self) -> str:
        if not self.symbol:
            return "symbol tidak terbaca"
        if not self.entries:
            return "entry tidak terbaca"
        if not self.take_profits:
            return "take profit tidak terbaca"
        if self.stop_loss is None:
            return "stop loss tidak terbaca"
        if self.side == "Buy" and self.stop_loss >= min(self.entries):
            return "LONG tapi SL di atas entry"
        if self.side == "Sell" and self.stop_loss <= max(self.entries):
            return "SHORT tapi SL di bawah entry"
        if self.side == "Buy" and any(t <= max(self.entries) for t in self.take_profits):
            return "LONG tapi ada TP di bawah entry"
        if self.side == "Sell" and any(t >= min(self.entries) for t in self.take_profits):
            return "SHORT tapi ada TP di atas entry"
        return "ok"


@dataclass
class SLUpdate:
    """Pesan susulan yang mengubah stop loss posisi berjalan."""
    symbol: str | None
    stop_loss: float
    raw_text: str = ""


def _nums(text: str) -> list[float]:
    out = []
    for m in re.findall(NUM, text):
        try:
            v = float(m)
        except ValueError:
            continue
        if v > 0:
            out.append(v)
    return out


def _strip_numbering(line: str) -> str:
    """Buang penomoran di awal baris: '1) 0.24' -> '0.24'."""
    return re.sub(r"^\W*\d{1,2}\s*[\)\.\-:]\s+", " ", line)


def _extract_sections(text: str) -> dict[str, list[float]]:
    """Pindai baris demi baris, kumpulkan angka ke bagian yang sedang aktif."""
    sections: dict[str, list[float]] = {"entry": [], "tp": [], "sl": []}
    current: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Baris penghenti (leverage, published by, dll)
        if _H_STOPWORD.match(line):
            current = None
            continue

        # Deteksi header. Urutan penting: entry & TP lebih spesifik dari 'targets'
        matched = None
        for key, pat in (("entry", _H_ENTRY), ("tp", _H_TP), ("sl", _H_SL)):
            m = pat.match(line)
            if m:
                matched = (key, line[m.end():])
                break

        if matched:
            key, rest = matched
            current = key
            vals = _nums(rest)
            if vals:
                sections[key].extend(vals)
            continue

        # Bukan header: kalau ada bagian aktif, ambil angkanya
        if current:
            cleaned = _strip_numbering(line)
            # Abaikan baris yang isinya hanya simbol/emoji
            vals = _nums(cleaned)
            if vals:
                sections[current].extend(vals)

    return sections


def parse_sl_update(text: str) -> SLUpdate | None:
    """Kenali pesan 'Set stoploss 0.0214'."""
    if not text:
        return None
    m = _RE_SET_SL.search(text)
    if not m:
        return None
    sym = None
    ms = _RE_SYMBOL.search(text)
    if ms:
        sym = (ms.group(1) + ms.group(2)).upper()
    else:
        # "#SKR" tanpa /USDT
        mh = re.search(r"[#$]([A-Z][A-Z0-9]{1,14})\b", text)
        if mh:
            sym = mh.group(1).upper() + "USDT"
    return SLUpdate(symbol=sym, stop_loss=float(m.group(1)), raw_text=text)


def parse_signal(text: str) -> Signal | None:
    """Kembalikan Signal bila teks terlihat seperti sinyal entry baru."""
    if not text:
        return None

    t = text.replace("\u00a0", " ").replace("\u2013", "-").replace("\u2014", "-")

    # Pesan status ("hit stoploss", "all targets achieved") bukan sinyal entry
    if re.search(r"\b(hit\s+stop|all\s+targets?\s+achieved|target\s+\d+\s+(hit|achieved)|"
                 r"closed\s+in\s+profit|cancel(?:led)?)\b", t, re.IGNORECASE):
        return None

    ms = _RE_SYMBOL.search(t)
    if not ms:
        return None
    symbol = (ms.group(1) + ms.group(2)).upper()

    if re.search(r"\bLONG\b", t, re.IGNORECASE):
        side = "Buy"
    elif re.search(r"\bSHORT\b", t, re.IGNORECASE):
        side = "Sell"
    else:
        return None

    raw_lev = None
    lm = re.search(r"leverage[^0-9]{0,20}(\d{1,3})\s*[xX\u00d7]", t, re.IGNORECASE)
    if lm:
        raw_lev = int(lm.group(1))

    sec = _extract_sections(t)

    entries = sec["entry"]
    tps = sec["tp"]
    sl = sec["sl"][0] if sec["sl"] else None

    # Buang duplikat sambil menjaga urutan
    def dedupe(xs):
        seen, out = set(), []
        for x in xs:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    entries, tps = dedupe(entries), dedupe(tps)

    if not entries or not tps:
        return None

    return Signal(
        symbol=symbol, side=side, entries=entries, take_profits=tps,
        stop_loss=sl, raw_leverage=raw_lev, raw_text=text,
    )
