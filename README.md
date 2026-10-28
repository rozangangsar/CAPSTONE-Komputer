# CAPSTONE-Komputer

Aplikasi localhost untuk akuisisi UDP, kalibrasi sesi, CSV dan analisis 1D CNN
setelah Stop. Python menangani seluruh data dan model; browser menampilkan
heatmap dan grafik. Tidak perlu internet saat digunakan, hosting, Node atau build frontend.

## Jalankan

Python 3.11/3.12 x64. Instal runtime CNN satu kali:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\run.ps1
```

Launcher membuka http://127.0.0.1:8765. Port lain: `.\run.ps1 -WebPort 8766`.
Alternatif tanpa script: `.\.venv\Scripts\python.exe web_server.py`.
Antarmuka Tkinter lama: `.\run.ps1 -Desktop`, dengan pilihan CNN realtime lama.

Demo: `.\run.ps1 -Demo`. Dua simulator UDP memakai port loopback terpisah dan
30 Hz. Demo mulai diam agar kalibrasi bisa dicoba; pilih Berjalan untuk
simulasi langkah. Label sesi SIMULATED, bukan data penelitian.
Ctrl+C di terminal menutup server dan menyelesaikan rekaman. Menutup tab saja
tidak menghentikan backend atau rekaman yang masih berjalan.

## Alur web

1. Laptop dan ESP berada pada Wi-Fi LAN yang sama. Cari IPv4 dengan ipconfig;
   firmware mengirim ke IP laptop, UDP port 9000.
2. Mulai menerima. Status, mask kanal, usia paket, IMU dan sync diperiksa otomatis.
3. Kalibrasi tekanan selama 10 detik tanpa beban. Kalibrasi IMU selama 3 detik diam.
4. Isi ID subjek, aktivitas dan catatan, lalu Mulai rekam.
5. Stop & analisis menutup file raw terlebih dahulu, kemudian menjalankan CNN
   pada CSV yang tersimpan di worker terpisah.
6. Unduh dataset sebagai ZIP. Riwayat sesi menyediakan analisis ulang.

Kalibrasi direkomendasikan tetapi tidak menjadi syarat merekam raw untuk
investigasi. Satu kaki dapat dilihat/direkam; CNN bilateral membutuhkan keduanya.
Tidak ada tombol periksa/reset. Kalibrasi ulang memakai tombol yang sama dan
mengganti profil lama hanya setelah berhasil. Kalibrasi terkunci selama rekaman.
Device/boot ID berubah menonaktifkan profil kaki tersebut.

## Kalibrasi dan filter

Baseline tekanan memakai median raw FDC tiap kanal. Respons sesi bertanda adalah
(baseline - raw) / 2000. Kestabilan disaring dengan std / baseline <=0.001 dan
minimal 70% sampel yang diharapkan. Ini ambang engineering, bukan bukti akurasi.
Operator tetap harus memastikan kondisi tanpa beban; beban konstan tidak bisa
dikenali hanya dari kestabilan. Raw FDC dan mask valid wajib untuk baseline.
Warning amplitudo belum dikirim firmware, sehingga belum dapat disertifikasi.

Kalibrasi IMU mengurangi bias gyro saja. Accel termasuk gravitasi dipertahankan;
bukan kalibrasi offset/scale accel enam posisi. Pemeriksaan diam mengikuti
BMI160 saat ini, ACC_RANGE=0x03 (+/-2g), 16384 counts/g. Gerakan, data putus,
kanal invalid, reboot atau sampel kurang menggagalkan kalibrasi baru.

Diproses menampilkan baseline sesi jika ada, lalu median kausal 3 sampel + EMA
tau 0.06 s. Jika belum dikalibrasi, filter memakai respons firmware.
Respons ESP menampilkan nilai firmware asli. Raw tetap disimpan. Filter menambah
delay; perlu uji tekan-lepas dan berjalan sebelum dipakai untuk riset.
Heatmap memakai skala bersama 0-1200 response counts, bukan persen beban/kPa.

## Kontrak dan model

sensor_contract.py: 30 Hz, 8 respons + 6 IMU per kaki, 60 sampel/jendela (~2 s),
stride 15 (~0.5 s). Urutan kanal tetap A CH0..3 lalu B CH0..3 sesuai mapping HP.
CSV/model IMU kanonik ax,ay,az,gx,gy,gz, raw signed counts. Parser mengoreksi
wire firmware FDC yang mengirim gyro dahulu; JSONL menyimpan salinan wire.

Model disertakan masih FUNCTIONAL_TEST_ONLY: weights legacy 5-FSR unilateral
mirrored, tensor internal [1,200,28], adapter linear 60->200 sebelum normalisasi
lama mean/std. Adapter tidak mengganti training pada sensor kapasitif saat ini.

Model default preprocessing=firmware_response: p1..p8 dan IMU raw kanonik.
Kalibrasi/filter baru tidak diam-diam disisipkan ke input weights lama.
Model baru dapat mendeklarasikan preprocessing=host_calibration_v1 setelah
benar-benar dilatih dengan filtered1..8 dan corrected_ax..gz, disertai mean/std
yang sesuai. Mode itu menuntut profil kalibrasi kedua kaki.

GaitWindows dipakai bersama mode realtime dan offline: grid host 30 Hz dari
ESP timestamp + offset sync, interpolasi interval sumber maksimal 80 ms,
RTT <=20 ms, umur sync <=30 s, selisih sampel-penerimaan <=100 ms.
Gate berjalan memakai variasi jumlah delapan respons tiap kaki. Data diam,
satu kaki, sync hilang, atau tidak cukup jendela menghasilkan Data belum cukup,
bukan NORMAL. Ringkasan memakai rata-rata skor semua jendela valid dan ambang
metadata model. Confidence/proporsi jendela belum merupakan probabilitas klinis.

## Dataset

```text
recordings/<UTC>_<id>/
  metadata.json
  left.csv
  right.csv
  packets.jsonl
  predictions.csv
  analyses/<analysis_id>/
    predictions.csv
    result.json
```

Kolom lama dipertahankan: timestamp, identitas, seq, sample index, fs_hz,
sequence status, offset/RTT/umur sync, p1..p8, ax..gz, raw1..raw8.
p1..p8 adalah respons firmware dari baseline boot yang di-clamp, bukan raw FDC.
Kolom tambahan: sensor_valid_mask, imu_valid, cal1..8, filtered1..8,
corrected_ax..gz. Nilai tak tersedia kosong. Metadata mencatat profil kalibrasi
pada awal sesi dan parameter pemrosesan. JSONL tidak diubah kalibrasi host.

File kaki yang tidak ada tetap hanya header; tidak dicerminkan atau diisi nol
seolah sampel nyata. Duplicate/out-of-order tetap disimpan dalam raw. File
predictions.csv di root merupakan keluaran realtime lama; mode web memakai
analyses/<id>/predictions.csv. Analisis ulang membuat folder baru.

Ekspor ZIP membawa seluruh folder sesi tanpa konversi kolom. Sesuaikan kontrak
training BRIN ke 30 Hz/60 sampel serta pilihan respons firmware atau hasil
kalibrasi yang digunakan; snapshot BRIN 100 Hz lama perlu diselaraskan sebelum
melatih model riset dengan kontrak sensor terbaru.

## UDP dan pemulihan

UDP v1: UTF-8 JSON, satu sample/datagram, maksimal 4096 byte. Sampel memiliki
v,type,foot,device_id,boot_id,seq,sample_index,t_us,fs_hz,pressure[8]; opsional
raw[8],imu[6],sensor_valid_mask,imu_valid. Boot ID berubah setiap reboot.
t_us harus waktu akuisisi. Raw yang tidak dikirim tidak bisa dikalibrasi host.

Host sync berisi v=1,type=sync,nonce,h1_us. ESP membalas dari socket yang sama:
v=1,type=sync_reply,foot,device_id,boot_id,nonce,e2_us,e3_us. Host mencatat h4.
Offset=((h1-e2)+(h4-e3))/2; RTT=(h4-h1)-(e3-e2). Ini estimasi delay simetris,
belum fitting drift/jaminan sub-milidetik. Validasi fisik tetap diperlukan.

Source dikunci ke device ID + IP/port pertama, bukan autentikasi. Protokol
untuk LAN riset tepercaya. 0.0.0.0 adalah bind, bukan tujuan ESP. Izin firewall
jaringan privat mungkin diperlukan; jangan menonaktifkan firewall global.
Celah seq bukan persentase loss final. Raw tidak diam-diam diinterpolasi.

Data di-flush sekitar satu detik dan saat stop, bukan fsync/jaminan tahan mati
listrik. Sesi error/crash adalah sesi parsial, bukan complete. Raw tersimpan
sebelum analisis; kegagalan model tidak membuang rekaman. Status complete hanya
berarti penutupan normal, bukan jaminan tidak ada loss atau sensor valid.

## Lokal dan desain

HTTP bind hanya 127.0.0.1; UDP bind 0.0.0.0. API memvalidasi Host/Origin,
tidak membuka CORS, dan membatasi ekspor ke folder sesi. Tidak menerima path
bebas. Riwayat menampilkan 100 sesi terakhir; tabel hasil 200 jendela pertama,
file hasil tetap lengkap. Frontend dan font tidak memakai CDN.
PRODUCT.md / DESIGN.md mencatat referensi Awwwards yang diperiksa, font dari
mbugm, dan mapping HP. Lisensi OFL di web/assets/fonts. Thumbnail referensi
hanya untuk riset desain, tidak disajikan oleh web server.

## Pengujian

```powershell
.\.venv\Scripts\python.exe -m unittest -v
node --check web/app.js
```

Tes mencakup UDP, sync, validity, raw/kanonik CSV, CNN asli, kalibrasi gagal dan
reboot, preservasi gravitasi, keamanan localhost, stop-ke-analisis dan ZIP.
Simulator tidak memvalidasi firmware fisik, respons filter saat berjalan,
kualitas sensor atau akurasi penelitian.
