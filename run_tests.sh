#!/usr/bin/env bash
set -e
echo "### 1/2  Uji parser (4 sinyal asli dari 3 channel)"
python test_parser.py
echo
echo "### 2/2  Uji alur penuh dengan Bybit tiruan"
python test_flow.py
echo
echo "SEMUA TES LULUS."
