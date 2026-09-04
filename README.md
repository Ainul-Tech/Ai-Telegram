# Telegram → Bybit Auto Trade Bot

Membaca sinyal dari 3 channel Telegram, eksekusi otomatis di **Bybit USDT
Perpetual**, plus dashboard riwayat.

Status uji: **70 tes lulus, 0 gagal** (`./run_tests.sh`).

---

## 1. Aturan yang dipastikan

| # | Aturan | Implementasi | Diuji di |
|---|---|---|---|
| 1 | Trade 25% dari aset | `ENTRY_EQUITY_PERCENT=25` → margin 25% equity | Skenario A |
| 2 | Leverage 10x | `LEVERAGE=10`, angka sinyal (50x) diabaikan | Skenario A & C |
| 3 | TP1 kena → SL = harga entry | `remove_stop_loss()` lalu `set_stop_loss(entry)` | Skenario A & B |
| 4 | TP1 50%, TP2 50% | `TP_COUNT=2`, `TP_SPLIT=[0.5,0.5]`, TP3 dibuang | Skenario A |
| 5 | Buy & sell otomatis | Limit order + reduce-only, tanpa campur tangan | Semua |
| 1b | Satu entry saja | `ENTRY_MODE=nearest` → entry terdekat harga pasar, ukuran penuh | Skenario F |
| 6 | Platform Bybit | Bybit V5 API lewat `pybit`, kategori `linear` | Semua |
| 7 | Diuji sebelum diserahkan | 29 tes parser + 27 tes alur | `run_tests.sh` |
| 8 | Siap GitHub | `.gitignore`, `Dockerfile`, `Procfile` | — |
| 9 | Cara aktivasi | Bagian 4 di bawah | — |
| 10 | Cara kerja + simulasi | Bagian 5 & 6 di bawah | — |

---

## 2a. Bagaimana bot memfilter channel

Bot memakai `events.NewMessage(chats=entities)` dari Telethon. Filternya bekerja
di level event: hanya pesan dari channel yang tercantum di `TG_CHANNELS` yang
sampai ke handler. Grup lain yang kamu ikuti **tidak dibaca sama sekali** —
bukan dibaca lalu dibuang.

Resolusi dilakukan sekali saat startup dan hasilnya ditulis di logs:
```
Channel OK: Global Crypto Research
Channel OK: Crypto World Updates
Channel OK: THE WOLF (CRYPTO)
FILTER AKTIF: hanya 3 channel di atas yang dibaca.
```
Kalau satu saja gagal diresolusi (salah ketik, channel privat, kamu belum
join), **bot berhenti dan tidak jalan** — supaya kamu tidak mengira semua
channel terpantau padahal hanya sebagian.

Untuk channel publik pakai `@username`. Untuk channel privat tanpa username
pakai ID numerik `-100xxxxxxxxxx`. Cara termudah mendapatkannya: halaman
`/channels` di service login (langkah 4d).

## 2. Format sinyal yang didukung

Parser diuji dengan pesan **asli** dari ketiga channel:

**Global Crypto Research** — `Entries: 0.0238 - 0.0228`, `Targets: a, b, c`
**Crypto World Updates** — `Entry Targets:` lalu `1) x` `2) y` bernomor
**THE WOLF SCALPER** — `ENTRY PRICE:` + `2nd ENTRY:`, TP dipisah tanda hubung

Pesan berikut otomatis **diabaikan**: promo/DM, `#SKR hit stoploss`,
`All targets achieved`, dan pesan "Recovery" (formatnya tanpa SL).
Pesan `Set stoploss 0.0214` **diterima** sebagai update SL posisi berjalan.

---

## 3. Isi repo

| File | Fungsi |
|---|---|
| `signal_parser.py` | Parser 3 format + validasi + deteksi update SL |
| `bybit_client.py` | Wrapper pybit: rounding, leverage, order, TP/SL |
| `trade_manager.py` | Sizing, eksekusi, monitor, circuit breaker |
| `history.py` | SQLite + penarik eksekusi & closed PnL dari Bybit |
| `dashboard.py` | Dashboard web (Flask + Chart.js) |
| `main.py` | Listener Telegram |
| `gen_session.py` | Buat session string untuk deploy cloud |
| `tests/` | Fixture sinyal asli, mock Bybit, 2 suite tes |
| `preflight.py` | Cek kesiapan akun Bybit & Telegram sebelum trial (read-only) |
| `railway.json` | Konfigurasi deploy Railway (auto-restart) |
| `login_web.py` | Halaman login Telegram via browser (dipakai sekali, lalu dihapus) |

---

## 4. Cara mengaktifkan, langkah demi langkah

### Langkah 1 — Ambil kredensial Telegram
1. Buka https://my.telegram.org, login dengan nomor HP kamu
2. Pilih **API development tools**, isi form apa saja (App title: `tradebot`)
3. Catat **api_id** (angka) dan **api_hash** (huruf-angka panjang)

### Langkah 2 — Buat API key Bybit
1. Login Bybit → ikon profil → **API** → **Create New Key**
2. Pilih **System-generated API Keys**
3. Permission yang dicentang: **Contract → Orders**, **Contract → Positions**,
   **Wallet → Account Transfer (read)**
4. **JANGAN** centang Withdraw
5. Batasi ke IP server kamu jika sudah tahu IP-nya
6. Untuk testnet, ulangi di https://testnet.bybit.com (key terpisah)

### Langkah 3 — Upload ke GitHub
```bash
git init
git add .
git commit -m "telegram bybit bot"
git branch -M main
git remote add origin https://github.com/USERNAME/NAMA-REPO.git
git push -u origin main
```
`.gitignore` sudah memblokir `.env`, `*.session`, dan `*.db`. **Pastikan
API key tidak pernah ikut ter-commit.**

### Langkah 4 — Deploy ke Railway (semua dari HP, tanpa komputer)

**4a. Buat project**
1. railway.app → login pakai GitHub
2. **New Project** → **Deploy from GitHub repo** → pilih repo kamu
3. Deploy pertama akan gagal — itu normal, variables belum diisi

**4b. Isi Variables**
Tab **Variables** → **Raw Editor** → tempel ini, ganti dengan nilai asli:
```
TG_API_ID=1234567
TG_API_HASH=isi_punyamu
TG_CHANNELS=@channel1,@channel2,@channel3
TG_SESSION_STRING=
BYBIT_API_KEY=isi_punyamu
BYBIT_API_SECRET=isi_punyamu
BYBIT_TESTNET=false
LEVERAGE=10
ENTRY_EQUITY_PERCENT=25
ENTRY_MODE=nearest
TP_COUNT=2
RESET_SL_TO_ENTRY=true
MAX_DRAWDOWN_PERCENT=50
DRY_RUN=true
REQUIRE_CONFIRM=false
MAX_CONCURRENT_POSITIONS=1
DB_PATH=/data/trades.db
```
`TG_SESSION_STRING` sengaja dikosongkan dulu — diisi di langkah 4d.

**4c. Pasang Volume** (wajib)
**Settings** → **Volumes** → **Add Volume** → mount path `/data`.
Tanpa ini, `trades.db` hilang tiap redeploy: riwayat lenyap dan patokan
circuit breaker ter-reset ke equity saat itu.

**4d. Login Telegram lewat browser**
Karena tidak ada terminal untuk memasukkan OTP, dipakai halaman login web.
1. Di project yang sama → **New** → **GitHub Repo** → pilih repo yang sama
2. Beri nama `telegram-login`
3. **Variables**: cukup `TG_API_ID` dan `TG_API_HASH`
4. **Settings** → Start Command: `python login_web.py`
5. **Settings** → **Networking** → **Generate Domain**
6. Buka URL itu di browser HP → masukkan nomor HP → Telegram mengirim kode
   ke **aplikasi Telegram** (bukan SMS) → masukkan kode (+ password 2FA bila ada)
7. Salin session string yang muncul
8. Ketuk link **"Lihat daftar channel & ID-nya"** → semua grup/channel yang
   kamu ikuti muncul beserta identifier persisnya. Ketuk identifier untuk
   menyalin. Ambil **hanya 3** yang mau dipantau, gabungkan dengan koma
9. Kembali ke service bot → **Variables** → isi `TG_SESSION_STRING` dan
   `TG_CHANNELS`
10. **HAPUS service `telegram-login`** (Settings → Remove Service).
   Jangan biarkan hidup — URL-nya bisa dipakai siapa saja yang menemukannya

**4e. Jalankan bot**
Service bot → **Settings** → Start Command: `python main.py` → **Deploy**

Buka tab **Deployments → View Logs**. Bot otomatis menjalankan pemeriksaan
pra-terbang dan menuliskan hasilnya di sini: izin API key, jenis akun, mode
posisi, ketersediaan symbol. Kalau ada `[XX]`, bot berhenti sendiri dan
menampilkan apa yang salah — perbaiki di Variables lalu redeploy.

**4f. Dashboard**
1. **New** → **GitHub Repo** → repo yang sama, beri nama `dashboard`
2. **Variables**: salin semua dari service bot, pastikan `DB_PATH=/data/trades.db`
3. **Settings** → **Volumes** → attach Volume `/data` **yang sama**
4. Start Command: `python dashboard.py`
5. **Networking** → **Generate Domain** → buka dari HP

**4g. Aktifkan uang sungguhan**
Setelah kamu lihat di dashboard bahwa beberapa sinyal terbaca dan dihitung
dengan benar, ubah `DRY_RUN=false` di Variables. Railway redeploy otomatis.

### Catatan khusus Railway

- **Jangan kunci API key Bybit ke IP.** IP keluar Railway tidak statis di paket
  dasar; bot akan ditolak Bybit. Cukup pastikan izin Withdraw mati.
- **`REQUIRE_CONFIRM` harus `false`.** Tidak ada terminal di cloud. Kalau
  `true`, bot melewati semua sinyal dan menulis error di log.
- **URL dashboard bersifat publik.** Siapa pun yang tahu alamatnya bisa
  melihat riwayat trading kamu. Jangan sebar.
- **Menghentikan darurat:** Settings → Remove Service. Untuk menutup posisi,
  pakai aplikasi Bybit langsung — jangan andalkan bot.

---

## 5. Cara kerja sistem

```
Pesan Telegram masuk (3 channel)
        │
        ├─ Cocok "Set stoploss X"? ──> update SL posisi berjalan
        ├─ Pesan status/promo?     ──> diabaikan
        │
        ▼
   Parse: symbol, arah, entry, TP, SL
        │
   Validasi: TP/SL di sisi benar? symbol ada di Bybit? posisi belum penuh?
        │
        ▼
   Set leverage 10x  (paksa, abaikan sinyal)
        │
   Hitung qty: margin = equity x 25%  →  notional = margin x 10
        │
   Pilih SATU entry: yang paling dekat harga pasar saat sinyal masuk
        │
   Pasang 1 limit order ukuran penuh di harga itu, SL menempel
        │
        ▼ (posisi terisi)
   Pasang TP1 & TP2 reduce-only, 50% : 50%   ← TP3 dibuang
        │
        ▼ (TP1 kena)
   Hapus SL lama → set SL baru di harga entry  (posisi bebas risiko)
        │
        ▼ (TP2 kena / SL kena / 12 jam tak terisi)
   Bersihkan order sisa → sinkron riwayat → cek drawdown
```

**Kenapa hanya satu entry:** kalau dua limit order dipasang 50/50, sering hanya
satu yang terisi. Posisi jadi separuh rencana sementara SL tetap di angka sinyal,
sehingga jarak entry-ke-SL meleset dari perhitungan. Dengan `ENTRY_MODE=nearest`,
entry dan risikonya pasti sejak awal. Ubah lewat `.env`:
`nearest` (default) / `first` (entry pertama di sinyal) / `split` (perilaku lama).

**Kenapa TP tidak ditempel di order entry:** Bybit `tpslMode: Full` menutup
**100%** posisi saat TP kena, sehingga aturan 50/50 tidak akan jalan. Karena
itu SL menempel di order (kritis, harus langsung aktif), sedangkan TP dipasang
sebagai order reduce-only terpisah begitu posisi terisi.

---

## 6. Simulasi dengan 4 sinyal asli (modal $20)

Margin $5 (25%), leverage 10x, notional $50. Sudah termasuk fee Bybit.

| Sinyal | Entry terpilih | Jarak SL | TP1+TP2 | TP1 lalu BE | Kena SL |
|---|---|---|---|---|---|
| SKR (GCR) | 0,0238 | 8,40% | +$4,49 | +$1,42 | **−$4,22** |
| USELESS (CWU) | 0,2330 | 6,44% | +$2,55 | +$0,71 | **−$3,24** |
| MARSCOIN (CWU) | 0,0885 | 5,65% | +$3,84 | +$1,10 | **−$2,84** |
| TRIA (WOLF) | 0,00540 | 5,19% | +$1,85 | +$0,58 | **−$2,61** |
| **Rata-rata** | | | **+15,9%** modal | **+4,8%** | **−16,1%** |

**Breakeven win rate:**
- Kalau menang selalu sampai TP2: butuh **50,3%** sinyal berhasil
- Kalau menang hanya sampai TP1 lalu breakeven: butuh **77,0%**

Kenyataannya campuran keduanya, jadi target realistis kamu ada di antara
**50% dan 77%**. Angka ini **lebih berat** dari mode split, karena entry
terdekat harga pasar selalu yang paling jauh dari SL. Aturan breakeven memang menghilangkan risiko setelah TP1,
tapi juga memotong banyak trade yang seharusnya lanjut ke TP2.

**Catatan dari data asli:** SKR di screenshot kamu **benar-benar kena stop
loss**. Itu satu sampel nyata dari kolom "Kena SL" — **−$4,22 atau −21% modal
dalam satu trade**, karena entry terpilih (0,0238) berjarak 8,4% dari SL.

---

## 7. Risiko yang harus kamu sadari

- **Notional 250% equity per posisi.** Pergerakan harga 1% = 2,5% equity.
  Satu SL rata-rata = **−16,1% modal**. Enam SL beruntun ≈ modal habis separuh
  dan circuit breaker menyala.
- **`MAX_CONCURRENT_POSITIONS` default 1.** Kalau dinaikkan ke 3, eksposur
  jadi 750% equity — pergerakan 13% berlawanan bisa melikuidasi akun.
  Dengan 3 channel aktif, banyak sinyal akan di-skip; itu disengaja.
- **Jarak SL tiap sinyal berbeda** (5,2% sampai 8,4% pada sampel ini, memakai
  entry terdekat). Karena
  ukuran posisi tetap, kerugian per trade ikut berbeda. Set `MAX_SL_PERCENT=8`
  untuk menolak sinyal dengan SL terlalu jauh.
- **Circuit breaker −50% itu longgar.** Turunkan ke 20-25% kalau mau lebih aman.
  Setelah menyala, bot tidak resume sendiri: hapus baris `start_equity` dan
  `halted_at` di tabel `meta` pada `trades.db`.
- **Leverage kontrak bisa di bawah 10x.** Banyak altcoin ada di Innovation
  Zone Bybit dengan batas 5x-12,5x. Bot otomatis menurunkan ke batas kontrak
  dan mengecilkan posisi agar margin tetap 25%. Cek dengan `preflight.py`.
- **Symbol mungkin tidak ada di Bybit.** Channel-channel itu memakai chart
  Binance. Kalau coin-nya tidak listing di Bybit, sinyal otomatis di-skip
  (tercatat di dashboard). Testnet Bybit apalagi — symbol-nya sangat sedikit,
  jadi DRY_RUN di testnet mungkin tidak pernah mengeksekusi apa pun.
- **Kualitas sinyal tidak bisa diperbaiki kode.** Semua angka di atas runtuh
  kalau win rate channel di bawah ambang breakeven. Kumpulkan 50-100 sinyal
  di mode DRY_RUN dulu dan hitung sendiri dari dashboard.
- Bot ini alat bantu eksekusi, bukan nasihat keuangan. Kerugian sepenuhnya
  tanggung jawab kamu.
