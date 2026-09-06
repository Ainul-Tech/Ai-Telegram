"""Pesan asli dari ketiga channel, dipakai untuk uji parser."""

GCR_SKR = """Global Crypto Research
Coin #SKR/USDT

Position: LONG

Leverage:  Cross 10x  To 50x

Entries:  0.0238 - 0.0228

Targets: 0.0250, 0.0265, 0.0288

Stop Loss: 0.0218

Published by @Liam_Ricardo1"""

CWU_USELESS = """#USELESS/USDT
SIGNAL Type: Regular (LONG)
Leverage: Cross (50X)
Amount: 1%

Entry Targets:

1) 0.2330

2) 0.2250

Take-Profit Targets

1) 0.2400

2) 0.2500

3) 0.2722

Stop Target:

1) 0.2180

Trailing Configuration:
Stop: Breakeven -
  Trigger: Target (1)"""

CWU_MARSCOIN = """#MARSCOIN/USDT
SIGNAL Type: Regular (LONG)
Leverage: Cross (50X)
Amount: 1%

Entry Targets:

1) 0.08850

2) 0.08600

Take-Profit Targets

1) 0.09250

2) 0.09900

3) 0.1050

Stop Target:

1) 0.08350"""

WOLF_TRIA = """THE WOLF SCALPER

LONG: $TRIA/USDT
LEVERAGE: 50x

ENTRY PRICE: 0.005400
2nd ENTRY: 0.005340
------------
TAKE- PROFIT

0.005530-0.005780-0.006200

STOP LOSS:  0.005120

CLICK TO JOIN BITUNIX

#WE_STAND_WITH_PALESTINE

REACTIONS?"""

# --- Pesan yang BUKAN sinyal entry -------------------------------------
NOT_SIGNAL_HIT_SL = "#SKR hit stoploss"
NOT_SIGNAL_PROMO = "DM now: @Liam_Ricardo1 | Join Premium & Earn!"
NOT_SIGNAL_ACHIEVED = "#USELESS/USDT All targets achieved"
NOT_SIGNAL_RECOVERY = """Recovery

Short #TRIA 0.0499
Tp 0.0481"""

# --- Pesan update SL ----------------------------------------------------
SL_UPDATE = """Coin #SKR/USDT  Position: LONG   Leverage...
Set stoploss 0.0214"""

# --- Sinyal cacat (harus ditolak) --------------------------------------
BAD_SL_WRONG_SIDE = """#FAKE/USDT
Position: LONG
Entries: 0.100
Targets: 0.120
Stop Loss: 0.150"""

BAD_SHORT_OK = """Coin #ABC/USDT
Position: SHORT
Entries: 1.200 - 1.250
Targets: 1.150, 1.100, 1.050
Stop Loss: 1.300"""


CM_DOGE = """Crypto Musk
Diteruskan dari VIP Crypto Musk
Long

Price Action : Nova Strategy

#DOGE/USDT

Entry :

1) 0.085730
2) 0.083158

Targets :

1) 0.086222
2) 0.088013
3) 0.089804
4) 0.091595

Stop : 0.080329

Leverage : 10x (isolated)

@crypto_musk1"""
