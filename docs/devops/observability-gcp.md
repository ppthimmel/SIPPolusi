# Observability di GCP (issue #112)

Laporan verifikasi dan pemulihan *Observability Service* pada deployment Google Cloud (project `sippolusi`, region
`asia-southeast2`). Isinya: kondisi awal, gap terhadap dokumen desain, penyelesaian, dashboard, mekanisme alert, dan
laporan pengujian. Konfigurasinya ada di [`src/observability/`](../../src/observability/README.md); perintah deploy di
[`scripts/gcp/README.md`](../../scripts/gcp/README.md#observability-issue-112).

**Grafana:** https://observability-87531247158.asia-southeast2.run.app (login `admin`; kata sandi di Secret Manager
`grafana-admin-password`). Dashboard **SIPPolusi overview** menjadi halaman awal.

> Catatan: tautan "dokumen desain" pada issue #112 menunjuk ke laporan proyek lain (IF5101, CouchDB). Laporan ini
> memakai dokumen desain SIPPolusi di repositori (`docs/Tugas 3 - Kelompok 1 - Dokumen Desain Penelitian Terapan.docx`,
> cabang `main`): Tabel 3.2 (Observability Service), kontrak IMetricsSink, dan EDO-02, EDO-03, EDO-04, EDO-07.

## 1. Kondisi awal (24 November 2026)

Diperiksa dengan `gcloud` dan Cloud Monitoring API (hanya baca).

| Yang diperiksa | Hasil |
|---|---|
| Layanan Cloud Run | `cache-api`, `frontend`, `gateway`, `model-registry`. **Tidak ada** Prometheus, Pushgateway, Alertmanager, maupun Grafana |
| Image di Artifact Registry | `cache-api`, `data-worker`, `frontend`, `gateway`, `model-registry`. Tidak ada image observability |
| `scripts/gcp/deploy.sh` | tidak punya langkah untuk observability |
| Cloud Monitoring | 0 alert policy, 0 notification channel, 0 dashboard, 0 uptime check |
| Cloud Logging | 0 metrik berbasis log. Log JSON Data Worker dan baris teks `route_inference` Cache API tersedia, tetapi tidak ada yang mengubahnya menjadi metrik |
| Konfigurasi di repo (`src/observability/`) | Prometheus memakai target Docker Compose/Railway (`backend:8000`, `pushgateway:9091`); tidak ada aturan alert, tidak ada Alertmanager, tidak ada dashboard Grafana (hanya datasource) |
| Instrumentasi | `/metrics` Backend dan Cache API: penghitung request dan **jumlah/total** latency (rata-rata saja, p95 tidak dapat dihitung). Penghitung fallback rute hanya ada di Backend lama, yang tidak di-*deploy*. Data Worker tidak mengirim metrik apa pun ke Pushgateway |
| Status yang dilaporkan | `docs/devops/gcp-deployment-status.md`: "Observability (Grafana / Prometheus): Not set up; logs are in Cloud Logging" |

## 2. Gap terhadap desain

| Persyaratan desain | Gap |
|---|---|
| Observability Service = Prometheus, Pushgateway, Grafana (Tabel 3.2) | Tidak satu pun berjalan di GCP |
| Lima komponen mengirim metrik lewat IMetricsSink (API Gateway, Backend, Multi-Objective Route Model, Spatial Downscaling Model, Data Worker), EDO-07 | API Gateway (NGINX) tidak punya `/metrics`; Route Model dan Downscaling Model tidak punya metrik sendiri; Data Worker tidak mem-*push* |
| Latensi per tahap, p95 < 1 menit (EDO-02) | Hanya rata-rata; tidak ada histogram sehingga p95 tidak terukur |
| Tingkat error < 1 %, uptime ≥ 99,9 % lewat probe sintetis (EDO-03) | Tidak ada probe dan tidak ada perhitungan rasio 5xx |
| Status pekerjaan periodik, ≥ 98 % eksekusi berhasil (EDO-04) | Hanya lewat log dan `python main.py status`; tidak ada metrik |
| Frekuensi fallback dan cakupan inferensi | Hanya di log |
| "Mengirim peringatan berbasis ambang"; "ketiadaan scrape memicu peringatan" (IMetricsSink) | Tidak ada aturan alert maupun Alertmanager |
| Konektivitas: Prometheus menjangkau layanan internal | Target masih nama Compose/Railway; Cache API dan registry di GCP ber-*ingress* internal sehingga hanya terjangkau dari VPC |

Kendala khusus Cloud Run yang memengaruhi penyelesaian:

- **Layanan berskala ke nol.** Prometheus yang men-*scrape* setiap 15 detik membuat Cache API (2 vCPU, 4 GiB, CPU selalu
  dialokasikan) tidak pernah berhenti; biayanya bulanan penuh.
- **Beberapa instance.** Satu *scrape* hanya mengenai satu instance Cache API, sehingga angka `/metrics` tidak lengkap
  bila ada dua instance.
- **Tidak ada disk.** Data Prometheus dan Pushgateway ada di memori instance.
- **Latensi Cloud Run tidak punya label path.** `request_latencies` gateway juga menghitung probe kesehatan.

## 3. Penyelesaian

```
                         internet
                            |  (login Grafana)
 +--------------------------v---------------------------------------------+
 | Cloud Run "observability" (1 instance, CPU selalu dialokasikan)        |
 |   grafana :3000 (ingress) --> prometheus :9090 --> alertmanager :9093  |
 |                                   |      \--> stackdriver_exporter :9255 --> Cloud Monitoring API
 |                                   |      \--> blackbox_exporter :9115 --> probe gateway, frontend
 +-----------------------------------|------------------------------------+
                                     | VPC (ingress internal)
                     Cloud Run "pushgateway" (internal) <-- Data Worker (push per siklus)
```

| Gap | Penyelesaian |
|---|---|
| Stack tidak berjalan | Layanan `observability`: Grafana sebagai container publik (wajib login), Prometheus, Alertmanager, stackdriver_exporter, blackbox_exporter sebagai *sidecar* di `localhost` (tidak terjangkau dari luar). Layanan `pushgateway` terpisah, *ingress* internal, 1 instance. Akun layanan `sippolusi-observability` (`roles/monitoring.viewer`). `scripts/gcp/deploy.sh observability-setup` dan `observability` |
| Target Compose/Railway | `entrypoint.sh` Prometheus menulis target `file_sd` dari env (`https://<layanan>-<nomor>.run.app`); job tanpa URL tidak punya target |
| API Gateway dan layanan Cloud Run tanpa `/metrics` | Metrik Cloud Run dari Cloud Monitoring lewat stackdriver_exporter (`request_count`, `request_latencies`, `instance_count`, CPU, memori), tanpa membangunkan layanan |
| p95 latensi rute (EDO-02) | Metrik berbasis log `sippolusi_gateway_route_latency`: distribusi `duration_s` dari access log NGINX untuk `POST /v1/routes` saja (definisi EDO-02: dari diterima gateway sampai respons) |
| Histogram di `/metrics` | `src/backend/telemetry.py`: `sippolusi_http_request_duration_seconds` menjadi histogram (bucket 0,05 s sampai 60 s) |
| Frekuensi fallback Route Model | Metrik berbasis log `sippolusi_route_inference` (label `outcome`, `fallback_reason`, `mode`, `solver`) dari baris `route_inference` Cache API; juga penghitung `sippolusi_route_solver_requests_total` dan histogram `sippolusi_route_solver_duration_seconds` di `/metrics` Cache API (`src/backend/solver/orchestrator.py`) |
| Data Worker dan Downscaling Model | `src/data_worker/metrics_push.py`: setiap siklus di-*push* (POST) ke Pushgateway: status siklus, umur sukses terakhir, status dan durasi langkah, cakupan inferensi, fallback IDW per polutan, sumber model, sebaran confidence. Gagal *push* hanya peringatan. Ditambah `completed_execution_count` Cloud Run Job |
| Probe sintetis (EDO-03) | blackbox_exporter memeriksa `gateway/gateway-health` dan `frontend/` setiap 60 detik |
| Cloud SQL | `database/up`, CPU, jumlah koneksi dari Cloud Monitoring |
| Alert | 16 aturan alert dan 12 aturan rekaman (`src/observability/prometheus/rules/sippolusi.rules.yml`), Alertmanager dengan *routing*, *grouping*, dan *inhibition*; diuji dengan promtool di CI |
| Cache API tetap hangat bila di-*scrape* | Tidak di-*scrape* secara bawaan; latensi, error, dan fallback-nya datang dari Cloud Monitoring. `CACHE_API_SCRAPE=1` menambah *scrape* `/metrics` selama uji beban (#111) |

## 4. Dashboard

**SIPPolusi overview** (`src/observability/grafana/dashboards/sippolusi-overview.json`, di-*provision*), refresh 1 menit:

| Baris | Panel |
|---|---|
| Status layanan | probe gateway/frontend (UP/DOWN), target scrape Prometheus (`up` per job), Cloud SQL, status siklus Data Worker terakhir, umur siklus sukses terakhir, tabel alert yang sedang aktif |
| Latency | p95 per layanan Cloud Run; p50/p95/p99 rute di gateway (EDO-02); waktu solver rute dan latency per route Cache API (saat di-*scrape*) |
| Error dan trafik | request/s per layanan dan kelas status; rasio 5xx per layanan (garis 1 % dan 5 %); hasil rute ok/fallback/failed per moda; frekuensi dan alasan fallback |
| Data Worker dan Spatial Downscaling Model | linimasa status siklus; eksekusi Cloud Run Job per hasil; tabel status langkah; cakupan inferensi dan fallback per polutan; sebaran confidence; durasi siklus dan inferensi |
| Sumber daya (uji beban #111) | instance aktif, CPU p95, memori p95 per layanan; CPU dan koneksi Cloud SQL |

## 5. Mekanisme alert

Aturan, ambang, dan alasannya ada di [`src/observability/README.md`](../../src/observability/README.md#alerts).
Ringkas: target scrape mati (5 menit), probe gagal (3 menit), 5xx > 1 % / > 5 %, p95 rute > 8 s / > 60 s, fallback rute
bike > 20 %, siklus Data Worker gagal atau dilewati, tidak ada siklus sukses > 2 jam, eksekusi job gagal, fallback IDW
> 2 jam, cakupan < 90 %, Cloud SQL mati, pembacaan Cloud Monitoring gagal, dan `Watchdog` yang selalu menyala.

Alertmanager mengelompokkan per alert dan layanan, menunggu 30 detik, mengulang setiap 12 jam, dan menahan alert 5xx dan
latensi selama probe layanan yang sama gagal. Penerima notifikasi diambil dari secret `alertmanager-config` (contoh:
`src/observability/alertmanager/alertmanager.example.yml`); tanpa secret itu alert tetap tercatat dan tampil di Grafana
(penerima `dashboard-only`).

## 6. Laporan pengujian

### 6.1 Uji otomatis

| Uji | Hasil |
|---|---|
| `promtool check rules` | SUCCESS, semua aturan valid |
| `promtool test rules tests/sippolusi.rules.test.yml`: setiap alert diuji kondisi menyala dan tidak menyala (termasuk volume request terlalu kecil, seri metrik yang baru muncul, fallback walk yang disengaja) | SUCCESS |
| `amtool check-config` (bawaan dan contoh) | SUCCESS |
| `src/backend/tests/test_observability.py` (histogram berurutan, `+Inf`, `_count`) dan seluruh `src/backend/tests` tanpa uji berbasis torch | 69 lulus, 1 dilewati |
| `src/backend/solver/tests` (fallback, orchestrator, API, flow; termasuk uji metrik solver baru) | 34 lulus, 6 dilewati |
| `src/backend/osm` | 122 lulus, 15 dilewati |
| `src/data_worker/tests/test_metrics_push.py` (POST mempertahankan sukses terakhir, gagal *push* tidak menggagalkan siklus dan tidak mencatat URL, `job_finished` mem-*push* status yang sama) | 6 lulus |
| Seluruh `src/data_worker/tests` (container Python 3.11, `Dockerfile.test`) | 395 lulus, 90 dilewati |

### 6.2 Uji lokal end-to-end (Docker Compose)

`src/observability/docker-compose.yml` dijalankan; dua siklus di-*push* lewat `metrics_push.push_cycle` (`degraded` lalu
`failed`).

- Pushgateway: setelah *push* `failed`, `sippolusi_job_last_success_timestamp_seconds` dan `sippolusi_downscale_*` dari
  *push* sebelumnya tetap ada (semantik POST).
- Prometheus: target `prometheus`, `pushgateway`, `alertmanager` `up`; 5 grup aturan dimuat tanpa galat.
- `DataWorkerCycleFailed` menyala dan diterima Alertmanager (penerima `dashboard-only`); `Watchdog` diterima dan
  diarahkan ke `blackhole`.
- Grafana: datasource Prometheus dan Alertmanager serta dashboard **SIPPolusi overview** ter-*provision*; datasource
  Alertmanager bekerja lewat proxy Grafana (tombol uji kesehatan datasource itu memang tidak didukung).

### 6.3 Uji di GCP (26 November 2026)

**Deploy.** `pushgateway` dan `observability` berjalan; kelima container mulai tanpa galat (log Cloud Run). Grafana
`/api/health`: `database: ok`, versi 13.0.12. `pushgateway` dari internet: HTTP 404 (internal).

**Konektivitas.** Semua target Prometheus `up`: `prometheus`, `alertmanager`, `pushgateway` (lewat VPC ke layanan
internal), `stackdriver`, `probe` gateway dan frontend. `stackdriver_monitoring_last_scrape_error = 0`. Metrik yang
terbaca dari Cloud Monitoring: `request_count`, `request_latencies`, `container/instance_count`, CPU dan memori per
layanan dan per job, `job/completed_execution_count`, Cloud SQL `database/up`, CPU, `num_backends`.

**Injeksi kegagalan.**

| Skenario | Cara | Hasil |
|---|---|---|
| Siklus Data Worker gagal | Cloud Run Job sementara di VPC mem-*push* `sippolusi_job_status{status="failed"} 1` ke Pushgateway | *Push* berhasil lewat VPC; `DataWorkerCycleFailed` aktif 08:03:17 UTC, diterima Alertmanager (`dashboard-only`) |
| Endpoint tidak menjawab 2xx | revisi Prometheus dengan probe tambahan `faultinjection` ke `gateway/does-not-exist-112` (404) | `probe_success = 0`; `EndpointProbeFailed` *pending* 08:04:13, *firing* dan diterima Alertmanager 08:07:13 (sesuai `for: 3m`) |
| Jalur alert hidup | `Watchdog` | *firing*, diterima Alertmanager, diarahkan ke `blackhole` |
| Metrik Data Worker belum pernah ada | kondisi nyata (kode #112 belum di-*deploy*) | `DataWorkerMetricsMissing` *pending*, menyala setelah 2 jam seperti dirancang |

Setelah uji, revisi diganti dengan konfigurasi normal (tanpa probe `faultinjection`), Pushgateway di-*deploy* ulang
(data uji hilang), dan job uji dihapus.

**Lalu lintas nyata.** `scripts/smoke_test.py` lewat gateway: 6/6 lulus. Dua permintaan rute moda *walk* tercatat
`outcome=fallback` pada metrik `sippolusi_route_inference`.

**Setelah perbaikan (revisi `observability-00003`, 08:41 UTC).** Enam rute tambahan lewat gateway (4 *bike*, 2 *walk*),
semuanya HTTP 200 dalam 0,5 sampai 2,1 detik.

- Target: `prometheus`, `alertmanager`, `pushgateway`, `stackdriver`, probe `gateway` dan `frontend` semuanya `up`;
  probe `faultinjection` sudah tidak ada.
- `sippolusi_route_inference`: `mode=bike, outcome=ok` 4; `mode=walk, outcome=fallback` 2.
- `sippolusi_gateway_route_latency`: 10 rute 2xx, p95 rute 0,97 s (target EDO-02 < 60 s).
- Rasio 5xx 0 untuk `cache-api`, `frontend`, `gateway`, `observability`, `pushgateway`.
- Alert: hanya `Watchdog` (*firing*) dan `DataWorkerMetricsMissing` (*pending*, menunggu kode #112 di-*deploy*);
  `DataWorkerCycleFailed` dan `EndpointProbeFailed` uji sudah selesai.
- Dashboard: 18 kueri panel berisi data, tidak ada galat kueri. Panel yang kosong: Data Worker dan inferensi (belum ada
  *push*), waktu solver dan latency per route Cache API (Cache API tidak di-*scrape*), dan tabel alert aktif (tidak ada
  alert selain `Watchdog`).
- CPU p95 instance `observability` 5,7 % dari alokasi, memori p95 41 %.

### 6.4 Temuan dan perbaikan selama pengujian

| Temuan | Perbaikan |
|---|---|
| p95 `request_latencies` gateway 0,009 s padahal rute smoke test 9,2 s: metrik Cloud Run tidak punya label path, sehingga probe 60 detik mendominasi pada lalu lintas rendah | Alert dan panel EDO-02 memakai metrik berbasis log `sippolusi_gateway_route_latency` (hanya `POST /v1/routes`) |
| Semua rute *walk* memakai fallback: `morl-route-policy:1` dilatih untuk *bike* ("MORL policy was trained for mode bike, not walk"); satu lagi karena *policy* masih dimuat setelah *cold start* | Label `mode` pada metrik; `RouteFallbackRateHigh` hanya untuk *bike*; dashboard memisahkan per moda |
| stackdriver_exporter membuat seri `result="failed"` saat kegagalan pertama, langsung bernilai 1, sehingga `increase()` tidak melihatnya | `DataWorkerExecutionFailed` dan panel eksekusi memakai selisih terhadap `offset 1h` (seri baru dihitung dari 0); diuji promtool |
| Rasio 5xx kosong untuk layanan tanpa 5xx | Aturan rekaman mengisi 0 (`or ... * 0`); diuji promtool |

## 7. Keterbatasan dan yang belum selesai

| Hal | Status / tindakan |
|---|---|
| Penerima notifikasi (email, Discord, dll.) | Belum diisi; alert hanya tampil di Grafana. Isi secret `alertmanager-config` (scripts/gcp/README.md), lalu uji dengan alert nyata |
| Data Worker mem-*push* metrik | Aktif setelah PR #112 masuk `dev` dan CI men-*deploy* `data-worker` (image baru dan `PUSHGATEWAY_URL`). Sampai itu `DataWorkerMetricsMissing` menyala; sesudahnya periksa panel Data Worker pada siklus menit ke-15 berikutnya |
| Data Prometheus di memori | Hilang saat revisi baru atau restart instance (retensi 15 hari / 256 MB). Karena itu `observability` tidak di-*deploy* oleh CI. Untuk bukti uji beban (#111), ekspor hasil kueri atau tangkapan dashboard selama uji |
| Basis data Grafana di memori | Dashboard dan datasource kembali dari *provisioning*, tetapi sesi login, pengguna tambahan, dan perubahan lewat UI hilang saat restart; semua orang perlu login ulang. Ubah dashboard lewat `src/observability/grafana/dashboards/` |
| Cache API tidak di-*scrape* bawaan | Pakai `CACHE_API_SCRAPE=1` hanya selama uji beban; dengan beberapa instance, satu *scrape* hanya mengenai satu instance |
| Metrik Cloud Monitoring terlambat 1 sampai 4 menit | Jendela alert 10 sampai 30 menit sudah memperhitungkannya |
| Biaya | `observability` 1,75 vCPU dan 1,6 GiB selalu dialokasikan, ditambah satu instance `pushgateway`; perkiraan kasar puluhan sampai sekitar seratus USD per bulan. Periksa Billing setelah beberapa hari dan atur *budget alert* (akun pemilik billing) |

## Lihat juga

- [`src/observability/README.md`](../../src/observability/README.md): konfigurasi, metrik per komponen, aturan alert
- [`docs/devops/observability.md`](observability.md): skema log dan health check (TI-DO-08)
- [`docs/devops/gcp-deployment-status.md`](gcp-deployment-status.md): status deployment GCP
