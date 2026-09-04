"""
gen_session.py — Buat TG_SESSION_STRING untuk deploy di cloud.

Jalankan SEKALI di komputer sendiri (butuh input OTP), lalu salin hasilnya
ke environment variable TG_SESSION_STRING di platform hosting.

    python gen_session.py

JANGAN pernah commit hasilnya ke GitHub. String ini setara akses penuh
ke akun Telegram kamu.
"""

import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv()

api_id = int(os.getenv("TG_API_ID") or input("TG_API_ID: "))
api_hash = os.getenv("TG_API_HASH") or input("TG_API_HASH: ")

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\n" + "=" * 60)
    print("TG_SESSION_STRING (salin seluruhnya, rahasiakan):")
    print("=" * 60)
    print(client.session.save())
    print("=" * 60)
