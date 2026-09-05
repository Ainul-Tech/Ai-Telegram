"""
login_web.py — Login Telegram lewat browser, tanpa komputer.

Dipakai sekali saja untuk menghasilkan TG_SESSION_STRING. Alurnya:
  1. Deploy service ini di Railway, buka URL-nya dari HP
  2. Masukkan nomor HP -> Telegram mengirim kode
  3. Masukkan kode (dan password 2FA bila ada)
  4. Salin session string yang muncul, tempel ke Variables Railway
  5. HAPUS service ini

Telethon butuh event loop yang hidup terus di antara request, jadi loop
dijalankan di thread terpisah dan coroutine dikirim ke sana.
"""

from __future__ import annotations

import asyncio
import os
import threading

from flask import Flask, redirect, request, url_for
from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

API_ID = int(os.getenv("TG_API_ID") or 0)
API_HASH = os.getenv("TG_API_HASH", "")

app = Flask(__name__)

# --- Event loop latar belakang ------------------------------------------
# Telethon butuh SATU event loop yang hidup terus. Kita jalankan loop itu di
# thread khusus, dan set sebagai loop aktif DI DALAM thread itu (penting:
# kalau tidak, Telethon di thread request Flask tidak menemukan event loop).
_loop = asyncio.new_event_loop()


def _loop_runner():
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


threading.Thread(target=_loop_runner, daemon=True).start()


def run(coro, timeout=90):
    """Jalankan coroutine di loop latar belakang, dari thread mana pun."""
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout)


state: dict = {"client": None, "phone": None, "hash": None}


async def _make_client() -> TelegramClient:
    # Dibuat di dalam _loop, jadi loop-nya sudah aktif di thread ini.
    return TelegramClient(StringSession(), API_ID, API_HASH)


def _client() -> TelegramClient:
    if state["client"] is None:
        state["client"] = run(_make_client())
    return state["client"]


# --- Tampilan ------------------------------------------------------------
CSS = """<style>
body{background:#0e1117;color:#e6edf3;font:16px/1.6 system-ui,-apple-system,sans-serif;
margin:0;padding:24px;display:flex;justify-content:center}
.box{max-width:460px;width:100%}
h1{font-size:20px;margin:0 0 6px}
p.sub{color:#8b949e;font-size:14px;margin:0 0 22px}
input{width:100%;padding:13px;margin:8px 0 16px;background:#161b22;border:1px solid #30363d;
border-radius:9px;color:#e6edf3;font-size:16px;box-sizing:border-box}
button{width:100%;padding:14px;background:#58a6ff;color:#04121f;border:0;border-radius:9px;
font-size:16px;font-weight:600;cursor:pointer}
label{font-size:13px;color:#8b949e}
.err{background:#3d1d1d;border:1px solid #f85149;color:#ffb4ae;padding:12px;
border-radius:9px;margin-bottom:16px;font-size:14px}
.ok{background:#12261a;border:1px solid #3fb950;color:#7ee2a0;padding:12px;
border-radius:9px;margin-bottom:16px;font-size:14px}
textarea{width:100%;height:190px;background:#161b22;border:1px solid #30363d;border-radius:9px;
color:#7ee2a0;font:12px/1.5 ui-monospace,monospace;padding:12px;box-sizing:border-box}
.warn{background:#2d2410;border:1px solid #d29922;color:#e3b341;padding:12px;
border-radius:9px;margin-top:16px;font-size:13px}
</style>"""


def page(body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'>" \
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>" \
           f"<title>Login Telegram</title>{CSS}</head><body><div class='box'>{body}</div></body></html>"


def err_box(msg: str) -> str:
    return f"<div class='err'>{msg}</div>" if msg else ""


# --- Rute ----------------------------------------------------------------
@app.route("/")
def index():
    if not API_ID or not API_HASH:
        return page(err_box(
            "TG_API_ID / TG_API_HASH belum diisi di Variables Railway. "
            "Isi dulu, lalu redeploy."))
    e = err_box(request.args.get("e", ""))
    return page(f"""
      <h1>Login Telegram</h1>
      <p class='sub'>Langkah 1 dari 2 — masukkan nomor HP akun Telegram kamu.</p>
      {e}
      <form method='post' action='/send'>
        <label>Nomor HP (pakai kode negara, contoh +6281234567890)</label>
        <input name='phone' placeholder='+62...' required>
        <button type='submit'>Kirim kode</button>
      </form>
      <div class='warn'>Halaman ini menghasilkan kunci akses penuh ke akun
      Telegram kamu. Jangan bagikan URL-nya, dan hapus service ini setelah
      selesai.</div>""")


@app.route("/send", methods=["POST"])
def send():
    phone = (request.form.get("phone") or "").strip()
    try:
        c = _client()
        run(c.connect())
        sent = run(c.send_code_request(phone))
        state["phone"] = phone
        state["hash"] = sent.phone_code_hash
        return redirect(url_for("code"))
    except Exception as ex:
        return redirect(url_for("index", e=f"Gagal mengirim kode: {ex}"))


@app.route("/code")
def code():
    e = err_box(request.args.get("e", ""))
    return page(f"""
      <h1>Masukkan kode</h1>
      <p class='sub'>Langkah 2 dari 2 — Telegram mengirim kode ke
      {state.get('phone')}. Cek aplikasi Telegram kamu, bukan SMS.</p>
      {e}
      <form method='post' action='/verify'>
        <label>Kode verifikasi</label>
        <input name='code' inputmode='numeric' placeholder='12345' required>
        <label>Password 2FA (kosongkan bila tidak pakai)</label>
        <input name='password' type='password' placeholder='opsional'>
        <button type='submit'>Masuk</button>
      </form>""")


@app.route("/verify", methods=["POST"])
def verify():
    code_in = (request.form.get("code") or "").strip()
    pwd = (request.form.get("password") or "").strip()
    c = _client()
    try:
        try:
            run(c.sign_in(phone=state["phone"], code=code_in,
                          phone_code_hash=state["hash"]))
        except SessionPasswordNeededError:
            if not pwd:
                return redirect(url_for("code", e="Akun ini pakai 2FA. Isi passwordnya."))
            run(c.sign_in(password=pwd))
        except PhoneCodeInvalidError:
            return redirect(url_for("code", e="Kode salah. Coba lagi."))
        except PhoneCodeExpiredError:
            return redirect(url_for("index", e="Kode kedaluwarsa. Ulangi dari awal."))

        me = run(c.get_me())
        sess = c.session.save()
        state["session"] = sess          # dipakai halaman /channels
        return page(f"""
          <h1>Berhasil</h1>
          <div class='ok'>Login sebagai {me.first_name} (@{me.username or '-'})</div>
          <p class='sub'>Salin seluruh teks di bawah, lalu tempel ke Variables
          Railway sebagai <b>TG_SESSION_STRING</b>.</p>
          <textarea readonly onclick='this.select()'>{sess}</textarea>
          <p style='margin-top:20px'>
            <a href='/channels' style='color:#58a6ff'>
            &rarr; Lihat daftar channel &amp; ID-nya (untuk TG_CHANNELS)</a></p>
          <div class='warn'>Setelah selesai: <b>hapus service login ini</b> di
          Railway. String ini setara akses penuh ke akun Telegram kamu.</div>""")
    except Exception as ex:
        return redirect(url_for("code", e=f"Gagal: {ex}"))


@app.route("/channels")
def channels():
    """Daftar semua channel/grup yang kamu ikuti, beserta identifier persisnya."""
    c = state.get("client")
    if not c or not state.get("session"):
        return redirect(url_for("index", e="Login dulu sebelum melihat channel."))
    try:
        run(c.connect())
        dialogs = run(c.get_dialogs(limit=500), timeout=120)
    except Exception as ex:
        return page(err_box(f"Gagal mengambil daftar: {ex}"))

    rows = []
    for d in dialogs:
        if not (d.is_channel or d.is_group):
            continue
        ent = d.entity
        uname = getattr(ent, "username", None)
        ident = f"@{uname}" if uname else f"-100{ent.id}"
        jenis = "channel" if d.is_channel else "grup"
        rows.append(f"""<tr><td>{d.name}</td><td>{jenis}</td>
            <td><code onclick="navigator.clipboard.writeText('{ident}')"
            style="color:#7ee2a0;cursor:pointer">{ident}</code></td></tr>""")

    tbl = ("<table style='width:100%;border-collapse:collapse;font-size:13px'>"
           "<tr style='color:#8b949e'><th align='left'>Nama</th>"
           "<th align='left'>Jenis</th><th align='left'>Identifier</th></tr>"
           + "".join(rows) + "</table>") if rows else "<p>Tidak ada channel.</p>"

    return page(f"""
      <h1>Channel yang kamu ikuti</h1>
      <p class='sub'>Ketuk identifier untuk menyalin. Ambil <b>hanya 3</b> yang
      mau dipantau, gabungkan dengan koma, lalu isikan ke Variables sebagai
      <b>TG_CHANNELS</b>. Contoh:<br>
      <code style='color:#7ee2a0'>@Channel1,-1001234567890,@Channel3</code></p>
      <div style='background:#161b22;border:1px solid #30363d;border-radius:9px;
      padding:14px;overflow-x:auto'>{tbl}</div>
      <div class='warn'>Channel yang TIDAK kamu masukkan ke TG_CHANNELS
      sama sekali tidak dibaca bot.</div>
      <p style='margin-top:16px'><a href='/' style='color:#58a6ff'>&larr; kembali</a></p>""")


@app.route("/health")
def health():
    return {"ok": True, "api_id_set": bool(API_ID)}


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
