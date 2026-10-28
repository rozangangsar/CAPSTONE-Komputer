# CAPSTONE-Komputer

## Konfigurasi sensor

Default **8 FSR per kaki**, sama dengan kontrak model HP. Pilih **4** pada pilihan FSR
per kaki untuk firmware trial saat ini sebelum menekan Mulai menerima.
Kedua ESP memakai JSON v1 dan satu port tujuan **9000**. Cari IP laptop dengan `ipconfig`.
Recording trial menyimpan p1..p4; p5..p8 kosong (tidak tersedia).
Firmware trial yang belum membalas UDP sync masih bisa direkam, tetapi belum bisa
dipakai untuk CNN. Firmware dan mapping pin ESP diatur di proyek hardware terpisah.

Aplikasi desktop Windows untuk menerima UDP dari dua smart insole dan merekam
dataset riset, dengan klasifikasi gait 1D CNN dari model uji aplikasi HP.
Python 3.11 atau 3.12 + Tkinter. Akuisisi raw memakai standard library;
CNN membutuhkan NumPy dan LiteRT. Autoencoder untuk BRIN tetap terpisah.

Instal runtime CNN satu kali (Windows x64):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`run.ps1` memakai `.venv` jika tersedia; jika tidak, memakai `py -3.11`.
Tanpa runtime CNN, penerima dan rekaman tetap berfungsi; kegagalan memuat model
ditampilkan di panel analisis.

## Klasifikasi CNN

1. Jalankan penerima dengan profil **8** dan kedua ESP pada **100 Hz**.
2. Setiap paket harus berisi 8 tekanan + 6 IMU; ESP harus membalas `sync`.
3. Klik **Mulai analisis CNN**, lalu berjalan untuk mengisi buffer.
4. Panel menampilkan `NORMAL` / `ABNORMAL`, confidence, P(abnormal), jumlah
   jendela yang dirata-ratakan, dan waktu inference. Klik **Mulai rekam** untuk
   menyimpan raw dan hasil prediksi pada sesi tersebut.

Model `models/gait_test_model.tflite` dan JSON metadata disalin dari aplikasi
CAPSTONEKOTLIN. Versi `test-legacy-v1`, purpose `FUNCTIONAL_TEST_ONLY`, sumber
`legacy_5fsr_bmi160_mirrored_unilateral`. Model ini **untuk uji pipeline**,
bukan model medis atau riset final. Menambah 8 kanal tidak membuat dataset
training legacy menjadi dataset bilateral 8 sensor. Urutan FSR, unit IMU,
orientasi dan kalibrasi harus sesuai training; jangan mengonversi raw IMU ke g
atau derajat/detik tanpa menyesuaikan normalisasi/model.

Kontrak model sama dengan HP: float32 `[1,200,28]`, urutan kiri 8 FSR + IMU
`ax,ay,az,gx,gy,gz`, lalu kanan dengan urutan yang sama. Normalisasi per kanal
`(raw - normalizationMean) / normalizationStd` dari JSON. Output adalah
P(abnormal), ambang 0.5; rata-rata sampai 10 jendela terakhir, stride 50 sampel
(0.5 detik). Confidence adalah skor kelas terpilih, bukan kepastian klinis.

Untuk input CNN saja, kedua kaki diinterpolasi linear ke grid host 100 Hz
berdasarkan timestamp ESP + offset sync. Interval sumber maksimal 25 ms
(paling banyak satu sampel 100 Hz hilang) dan RTT sync maksimal 20 ms;
sync kedaluwarsa setelah 30 detik. Ini estimasi, bukan jaminan presisi clock.
Sampel yang estimasi waktunya berbeda lebih dari 100 ms dari penerimaan ditolak untuk CNN.
Tidak ada penyamaan `sample_index` antar-ESP. Data raw CSV/JSONL tidak diubah.

Gate berjalan mengikuti aturan HP: variasi tekanan kiri P3, kanan jumlah
P2..P4, rentang minimal 40/80 raw dan minimal dua transisi dalam jendela 1 detik;
bukti berjalan harus bertahan 300 ms. Bukti berhenti kiri <=35 raw tanpa
transisi harus bertahan 1 detik. Ambang kalibrasi ada di `WalkingGate` pada
`cnn.py`. Setelah gate aktif, dibutuhkan 200 frame berjalan sebelum prediksi.
CNN tidak mengklasifikasikan aktivitas atau jenis penyakit.

Berbeda dari fallback kanal hilang di HP, desktop menahan prediksi jika IMU
tidak lengkap. Gap besar, reboot, sync tidak valid atau data terlambat akan
mengosongkan buffer dan riwayat rata-rata. Hasil tanpa pembaruan selama 1 detik
disembunyikan. Worker CNN terpisah dari UDP dan UI; antrean terbatas mencegah
inference menghambat akuisisi. Runtime/model error hanya menghentikan analisis.

## Jalankan

```powershell
cd C:\Users\ROZAN\source\repos\CAPSTONE-Komputer
.\run.ps1
```

Jika PowerShell membatasi script, jalankan `.\.venv\Scripts\python.exe app.py` langsung.
Python resmi Windows harus menyertakan Tcl/Tk. Uji dengan
`py -3.11 -c "import tkinter; print(tkinter.TkVersion)"`.

Demo tanpa hardware (dua ESP simulasi, port loopback bebas otomatis):

```powershell
.\run.ps1 -Demo
# Alternatif:
.\.venv\Scripts\python.exe app.py --demo
```

Tunggu kedua kaki aktif, lalu klik **Mulai rekam**. Sesi demo dilabeli
`SIMULATED-walking`; jangan dipakai sebagai data penelitian.
Demo berhenti saat jendela ditutup. Setelah menghentikan penerima dalam demo,
tutup dan jalankan ulang demo untuk reset kedua simulator dan portnya.
Tombol analisis dapat dipakai untuk menguji inference pada data simulasi,
tetapi label/confidence demo tidak memiliki makna penelitian.

## Akuisisi dari ESP

1. Sambungkan laptop dan kedua ESP ke Wi-Fi LAN yang sama.
2. Cari IPv4 adaptor Wi-Fi laptop dengan `ipconfig`, misalnya `192.168.1.20`.
3. Di firmware ESP, set tujuan UDP ke IP tersebut, port `9000`.
4. Di aplikasi, bind `0.0.0.0`, port `9000`, klik **Mulai menerima**.
5. Pastikan kedua kaki aktif; isi ID subjek, label aktivitas, dan catatan unit/kalibrasi.
6. Klik **Mulai rekam**, lakukan percobaan, kemudian **Hentikan & simpan**.
7. Transfer seluruh folder sesi ke BRIN melalui mekanisme transfer yang tersedia.

`0.0.0.0` adalah alamat bind lokal, bukan alamat tujuan ESP. VPN kantor tidak
dibutuhkan untuk akuisisi lokal. Jika Windows meminta izin firewall, izinkan
Python untuk jaringan privat yang digunakan. Jangan matikan firewall global.
Router dengan client isolation dapat menghalangi paket antardevice.

Setiap kaki memakai satu socket UDP dengan source port tetap selama sesi.
Aplikasi mengikat identitas kaki ke device ID + IP/port pertama yang diterima.
Device lain yang mengaku sebagai kaki yang sama ditolak sampai penerima dihentikan
dan dimulai ulang. Ini pencegah pencampuran perangkat, **bukan autentikasi**;
protokol v1 ditujukan untuk LAN riset tepercaya, bukan internet publik.

## File hasil

Setiap sesi memakai folder UTC + ID unik, tanpa menimpa sesi sebelumnya:

```text
recordings/20260907T...Z_a1b2c3d4/
  metadata.json
  left.csv
  right.csv
  packets.jsonl
  predictions.csv
```

- `left.csv`, `right.csv`: satu baris per paket sampel yang diterima, 8 tekanan,
  IMU opsional, timestamp ESP, timestamp penerimaan host, sequence status, dan
  estimasi waktu host jika tersedia. Raw disimpan termasuk duplikat/out-of-order.
- `packets.jsonl`: isi JSON paket yang lolos validasi/pemeriksaan identitas,
  source address, waktu penerimaan monotonic dan UTC. Balasan sinkronisasi juga
  disimpan dengan keempat timestamp agar dapat dianalisis ulang di server.
  Ini arsip semantik JSON, bukan pcap/byte asli. Paket invalid/ditolak hanya dihitung.
- `metadata.json`: subjek, label, catatan, konfigurasi awal perangkat, status sesi,
  jumlah baris per kaki, dan counter kualitas. Counter adalah sejak penerima
  dimulai; status seq di tiap baris dapat dipakai untuk analisis khusus sesi.
- `predictions.csv`: waktu awal/akhir jendela pada grid host, label, confidence,
  probabilitas per jendela dan rata-rata, latency, jumlah jendela rata-rata,
  versi model. Hanya hasil dengan seluruh jendela berada setelah awal rekaman
  yang disimpan; rata-rata dapat mencakup jendela analisis sebelum rekaman.
  Metadata menyimpan purpose, hash model/metadata, sumber training, serta
  jumlah prediksi. Label prediksi tidak mengganti label aktivitas subjek.

CSV berisi **nilai sensor mentah**, tidak otomatis kPa atau satuan fisik.
Dokumentasikan unit, sensor, orientasi IMU, kalibrasi dan versi firmware dalam
catatan. ID subjek lebih sesuai daripada nama pasien untuk nama dataset.
Ubah label dengan menghentikan sesi dan mulai sesi baru; metadata tidak berubah
di tengah satu sesi.

## UDP v1

UTF-8 JSON, satu sampel per datagram, maksimum **4096 byte**. JSON dipilih untuk
prototipe agar firmware mudah diperiksa; ukur loss/CPU sebelum pindah ke binary.
Tidak membutuhkan server cloud, MQTT, BLE ataupun socket library tambahan.

Contoh datagram dari ESP kiri:

```json
{
  "v": 1,
  "type": "sample",
  "foot": "left",
  "device_id": "insole-L01",
  "boot_id": "boot-a91f",
  "seq": 42,
  "sample_index": 42,
  "t_us": 123420000,
  "fs_hz": 100,
  "pressure": [120, 121, 118, 115, 110, 105, 100, 98],
  "imu": [0.01, 0.02, 1.0, 0.1, 0.0, -0.1]
}
```

`foot`: `left` atau `right`. `device_id`, `boot_id`: 1..64 karakter ASCII
alfanumerik / `-_.`. `boot_id` harus berubah setiap reboot. `seq`, `sample_index`,
`t_us`: integer nonnegatif 64-bit signed; jangan reset tanpa boot ID baru.
`seq` naik untuk setiap paket sampel yang dibuat, `sample_index` untuk setiap
akuisisi sensor. `fs_hz`: integer 1..2000, **target yang dideklarasikan firmware**,
bukan hasil ukur sampling. `t_us` adalah timestamp akuisisi dari ESP, bukan waktu
pengiriman. `pressure` harus tepat 8 angka finite (4 hanya pada profil trial). `imu` boleh tidak ada/null untuk rekaman;
jika ada, urutannya `[ax, ay, az, gx, gy, gz]` dan harus tepat 6 angka finite.
Satuan dan urutan sensor harus konsisten dengan pipeline training.

Firmware belum disertakan; jika firmware sudah punya format lain, adaptasi parser
`parse_packet()` setelah melihat contoh datagram nyata. Proyek HP tidak diubah.

## Sinkronisasi waktu v1

Host memakai `time.perf_counter_ns() // 1000` (clock monotonic resolusi tinggi
di Windows, termasuk Python 3.11/3.12). ESP sebaiknya memakai
`esp_timer_get_time()` yang dibaca sedekat mungkin dengan akuisisi sensor.
Host otomatis mengirim permintaan ke socket sumber masing-masing ESP:

```json
{"v": 1, "type": "sync", "nonce": "abc123", "h1_us": 123456789}
```

ESP membalas dari socket yang sama; tangkap `e2_us` saat menerima permintaan
dan `e3_us` sesaat sebelum mengirim balasan:

```json
{"v": 1, "type": "sync_reply", "foot": "left", "device_id": "insole-L01", "boot_id": "boot-a91f", "nonce": "abc123", "e2_us": 111000, "e3_us": 111050}
```

Host mencatat `h4` saat balasan diterima dan menghitung:

```text
host_offset = ((h1 - e2) + (h4 - e3)) / 2
rtt = (h4 - h1) - (e3 - e2)
estimated_host_sample_us = esp_t_us + host_offset
```

Delapan pengukuran awal dicoba tiap 250 ms, lalu tiap 5 detik. Pilih RTT terkecil
dari hasil 20 detik terakhir; estimasi kedaluwarsa setelah 30 detik. Timestamp
asli tidak diubah. Firmware tanpa sync masih bisa direkam, kolom estimasi kosong.
Estimasi mengasumsikan delay bolak-balik mendekati simetris. RTT kecil bukan bukti
kesalahan sinkronisasi sub-milidetik. Validasi memakai pemicu fisik bersama.

**Batas implementasi raw:** v1 merekam raw dan estimasi offset berkala. Belum ada
`START_AT`, kontrol sampling ESP, fitting drift atau klaim pasangan sample index
yang identik. Resampling/interpolasi hanya untuk input CNN, tidak untuk file raw. Tombol
rekam hanya mengatur penyimpanan lokal. Kedua ESP boleh mulai pada waktu berbeda;
sample index yang sama belum tentu waktu fisik yang sama. Di server, gunakan
log sync + timestamp raw untuk fitting `host_time = scale * esp_time + offset`,
lalu selaraskan ke grid waktu bersama. Jangan sejajarkan puncak gait kiri/kanan
secara paksa karena beda fase itu sendiri merupakan informasi yang diteliti.

## Kualitas dan pemulihan

- UDP tidak menjamin delivery. **Celah seq** menghitung lompatan sequence yang
  teramati, bukan persentase loss final: paket terlambat bisa menutup gap.
  Hilangnya paket pertama/terakhir tidak bisa diketahui tanpa laporan dari ESP.
- Duplikat dikenali untuk 4096 sequence terbaru. Paket sangat lama tetap disimpan;
  deduplikasi offline memakai device + boot + seq. Tidak ada interpolasi tersembunyi.
- UI diperbarui 5 kali/detik. Penerimaan dan penulisan berada di worker terpisah.
  Prototipe memakai I/O disk langsung dengan socket buffer 1 MiB yang diminta;
  OS dapat membatasi ukuran aktual. Disk lambat dapat menyebabkan kernel drop.
- Data di-flush sekitar setiap detik dan saat Stop/Close; ini bukan `fsync` dan
  tidak menjamin tahan listrik mati. Jika aplikasi crash, periksa status
  `recording` pada metadata, gunakan baris lengkap yang tersisa sebagai sesi parsial.
- Write failure menghentikan akuisisi dan menampilkan error. `status=error` dapat
  ditulis jika media masih memungkinkan. `complete` berarti file ditutup normal,
  **tidak** menjamin tidak ada loss atau bahwa kedua kaki aktif sepanjang sesi.
- Snapshot status perangkat memberi usia paket; `TIDAK ADA DATA` setelah 3 detik.
  Rekaman tetap terbuka bila satu kaki terputus, sehingga gap riset tetap terlihat.
- Restart ESP dengan source port tetap dicatat dan clock lama dibuang. Jika IP/port
  berubah, stop rekaman/penerima lalu mulai sesi baru untuk mengikat ulang perangkat.

## Pengujian

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

Tes meliputi validasi paket, sequence gap/duplikat/out-of-order/reboot, konflik
sender, hitungan sync, dua simulator UDP, arsip CSV/JSON, dan penutupan sesi.
Tes CNN meliputi window/channel/stride, gate berjalan, gap/reboot, input tidak
lengkap, normalisasi dan inference model asli, serta prediksi UDP-ke-CSV.
Tes model asli dilewati bila runtime belum dipasang.

Simulator terpisah untuk menguji loss dari terminal lain:

```powershell
py -3.11 simulator.py --host 127.0.0.1 --port 9000 --hz 100 --drop-every 20
```

Simulator sengaja bukan emulator timing hardware. Pengujian ESP fisik tetap
diperlukan untuk kestabilan sampling, loss Wi-Fi, clock drift dan beban disk.
# Current 30 Hz capacitive hardware update (2026-10-27)

Run `run.ps1` normally. Start UDP receiving, then record with **one or both**
live feet. Label engineering trials UNLABELLED/TEST_ONLY. Channel mapping and
canonical IMU accelXYZ/gyroXYZ are included in metadata. Response p1..p8 and
optional FDC raw1..raw8 are separate; missing foot is never fabricated.

ML public input is now60x28 at30Hz, stride15. The old functional-test network
internally remains200x28 with an explicit linear60->200 adapter, NOT retraining.
Bilateral inference requires two synchronized feet; recording works independently.
The full data/model contract is C:/esp/smart_insole_rtos/DATA_COLLECTION_30HZ.md.
Descriptions of the earlier100Hz runtime below are historical.

