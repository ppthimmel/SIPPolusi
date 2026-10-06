"""Orkestrasi satu lintasan pengumpulan."""

from __future__ import annotations

import datetime as dt
import logging

from .config import Config
from .extract import ExtractionError, extract_json, extract_scalar
from .fetch import Fetcher
from .normalize import UTC, parse_detail, parse_home
from .roster import initial_of, parse_list_page, parse_sitemap, reconcile
from .store import Store, now_utc

log = logging.getLogger("spku.collect")


class AbortedRun(RuntimeError):
    """Lintasan dihentikan sebelum menulis apa pun ke basis data."""


def collect_roster(fetcher: Fetcher, run_id: str, store: Store) -> dict[str, str]:
    listing = fetcher.get("/lokasi-spku")
    store.log_fetch(run_id, url=listing.url, fetched_utc=listing.fetched_utc,
                    http_status=listing.status, bytes=listing.bytes, sha256=listing.sha256,
                    elapsed_ms=listing.elapsed_ms, parse_ok=listing.ok, error=listing.error)
    from_list = parse_list_page(listing.text or "")

    sitemap = fetcher.get("/sitemap.xml")
    store.log_fetch(run_id, url=sitemap.url, fetched_utc=sitemap.fetched_utc,
                    http_status=sitemap.status, bytes=sitemap.bytes, sha256=sitemap.sha256,
                    elapsed_ms=sitemap.elapsed_ms, parse_ok=sitemap.ok, error=sitemap.error)
    from_sitemap = parse_sitemap(sitemap.text or "")

    merged, warnings = reconcile(from_list, from_sitemap)
    for warning in warnings:
        log.warning("daftar stasiun: %s", warning)
    return merged


def _harvest(fetcher: Fetcher, run_id: str, store: Store, targets: dict[str, str]):
    """Ambil dan urai seluruh halaman rinci. Belum menulis data apa pun."""
    parsed_all = []
    failures = 0
    for station_uuid, label in targets.items():
        result = fetcher.get(f"/spku/{station_uuid}")
        payload = None
        error = result.error
        archive_path = None
        if result.ok:
            try:
                payload = extract_json(result.text, "SPKU_DETAIL_DATA")
            except ExtractionError as exc:
                error = str(exc)
        if payload is None and error is None:
            error = "SPKU_DETAIL_DATA tidak ditemukan"
        if payload is None:
            failures += 1
            if result.text:
                archive_path = fetcher.archive_raw(run_id, station_uuid, result.text)
            store.log_fetch(run_id, url=result.url, fetched_utc=result.fetched_utc,
                            http_status=result.status, bytes=result.bytes,
                            sha256=result.sha256, elapsed_ms=result.elapsed_ms,
                            parse_ok=False, archive_path=archive_path, error=error)
            log.warning("%s (%s): %s", label or station_uuid, station_uuid, error)
            continue

        parsed = parse_detail(payload)
        if not parsed.station.uuid:
            parsed.station.uuid = station_uuid
        archive_path = fetcher.archive(run_id, station_uuid, payload)
        store.log_fetch(run_id, url=result.url, fetched_utc=result.fetched_utc,
                        http_status=result.status, bytes=result.bytes,
                        sha256=result.sha256, elapsed_ms=result.elapsed_ms,
                        parse_ok=True, n_rows=len(parsed.observations),
                        archive_path=archive_path)
        parsed_all.append((parsed, label, result.fetched_utc))
    return parsed_all, failures


def run_full(config: Config, only: list[str] | None = None) -> dict:
    """Lintasan penuh: daftar stasiun, lalu seluruh halaman rinci.

    Pengambilan dan penguraian berlangsung sepenuhnya sebelum penulisan
    dimulai, dan seluruh penulisan berada dalam satu transaksi. Lintasan
    yang gagal karena sebab apa pun tidak meninggalkan data separuh jadi.
    """
    store = Store(config.database, config.db_schema)
    fetcher = Fetcher(
        contact=config.contact,
        delay_seconds=config.delay_seconds,
        timeout=config.timeout_seconds,
        retries=config.retries,
        archive_dir=config.archive_dir or None,
    )
    run_id = store.start_run("full")
    log.info("lintasan %s dimulai", run_id)

    try:
        roster = collect_roster(fetcher, run_id, store)
        previous = store.known_station_uuids(active_only=True)

        # Penjaga tahap pertama: daftar stasiun kosong atau menyusut drastis.
        if not roster:
            raise AbortedRun("daftar stasiun kosong; lintasan dibatalkan tanpa menulis")
        if previous and len(roster) < config.min_roster_ratio * len(previous):
            raise AbortedRun(
                f"daftar stasiun menyusut dari {len(previous)} menjadi {len(roster)}; "
                "lintasan dibatalkan tanpa menulis"
            )

        targets = {
            k: v for k, v in roster.items()
            if not only or k in only or (initial_of(v) or "") in only
        }
        if only and not targets:
            raise AbortedRun(f"tidak ada stasiun yang cocok dengan {only}")

        parsed_all, failures = _harvest(fetcher, run_id, store, targets)

        # Penjaga tahap kedua: halaman terambil tetapi nol observasi. Inilah
        # pola gangguan 8 September 2026, yang menjawab HTTP 200 dengan
        # halaman utuh tanpa data sama sekali.
        reporting = [p for p, _, _ in parsed_all if p.is_reporting]
        if parsed_all and not reporting:
            raise AbortedRun(
                f"{len(parsed_all)} halaman terurai tetapi nol observasi; "
                "portal diduga sedang bermasalah"
            )
        # Penjaga tahap ketiga: terlalu banyak halaman gagal. Dibandingkan
        # terhadap jumlah sasaran, bukan terhadap seluruh stasiun yang
        # dikenal, agar penyaringan --only tetap dapat dipakai.
        if len(parsed_all) < config.min_roster_ratio * len(targets):
            raise AbortedRun(
                f"hanya {len(parsed_all)} dari {len(targets)} stasiun berhasil diurai; "
                "lintasan dibatalkan tanpa menulis"
            )
        # Halaman yang metadatanya terdegradasi tidak boleh ikut menulis.
        if only is None and len(reporting) < config.min_roster_ratio * len(targets):
            raise AbortedRun(
                f"hanya {len(reporting)} dari {len(targets)} stasiun mengirim observasi; "
                "lintasan dibatalkan tanpa menulis"
            )

        rows_new = rows_revised = 0
        with store.transaction():
            events = store.record_roster(roster)
            for station_uuid, label, event in events:
                log.warning("perubahan daftar: %s %s (%s)", event, label or "-", station_uuid)
            for parsed, label, fetched in parsed_all:
                store.upsert_station(parsed, initial=initial_of(label))
                new, revised = store.upsert_observations(parsed.observations)
                rows_new += new
                rows_revised += revised
                store.upsert_ispu(parsed.ispu_points)
                store.upsert_meteo(parsed.meteo)
                store.upsert_daily(parsed.daily)
                store.record_status(parsed, fetched)

        summary = {
            "run_id": run_id,
            "status": "ok" if failures == 0 else "partial",
            "stations_seen": len(targets),
            "stations_ok": len(parsed_all),
            "stations_reporting": len(reporting),
            "failures": failures,
            "rows_new": rows_new,
            "rows_revised": rows_revised,
        }
        store.finish_run(
            run_id, summary["status"],
            stations_seen=summary["stations_seen"], stations_ok=summary["stations_ok"],
            rows_new=rows_new, rows_revised=rows_revised,
            note=f"{len(reporting)} stasiun melaporkan, {failures} gagal diurai",
        )
        log.info("lintasan %s selesai: %s", run_id, summary)
        return summary

    except AbortedRun as exc:
        store.finish_run(run_id, "aborted", note=str(exc))
        log.error("lintasan %s dibatalkan: %s", run_id, exc)
        return {"run_id": run_id, "status": "aborted", "note": str(exc)}
    except Exception as exc:  # noqa: BLE001 - lintasan tidak boleh menggantung
        store.finish_run(run_id, "failed", note=f"{type(exc).__name__}: {exc}")
        log.exception("lintasan %s gagal", run_id)
        return {"run_id": run_id, "status": "failed", "note": f"{type(exc).__name__}: {exc}"}
    finally:
        fetcher.close()
        store.close()


def run_snapshot(config: Config) -> dict:
    """Lintasan ringan: satu permintaan ke beranda.

    Berfungsi sebagai pendeteksi gangguan portal dan pemantau kebasian per
    stasiun. Tidak menghasilkan observasi; nilai terbaru sudah tercakup oleh
    lintasan penuh.

    Kegagalan jaringan dan gangguan portal dibedakan dengan tegas. Keduanya
    sama-sama menghasilkan nol stasiun, tetapi hanya yang kedua yang boleh
    tercatat sebagai cuplikan, karena tabel cuplikan adalah bukti keadaan
    portal, bukan bukti keadaan jaringan pengumpul.
    """
    store = Store(config.database, config.db_schema)
    fetcher = Fetcher(config.contact, config.delay_seconds, config.timeout_seconds,
                      config.retries, archive_dir=config.archive_dir or None)
    run_id = store.start_run("snapshot")
    try:
        result = fetcher.get("/")
        payload = update_time = None
        error = result.error
        archive_path = None
        if result.ok:
            try:
                payload = extract_json(result.text, "__SPKU_DATA__")
                update_time = extract_scalar(result.text, "__SPKU_UPDATE_TIME__")
            except ExtractionError as exc:
                error = str(exc)
                archive_path = fetcher.archive_raw(run_id, "beranda", result.text)

        store.log_fetch(run_id, url=result.url, fetched_utc=result.fetched_utc,
                        http_status=result.status, bytes=result.bytes, sha256=result.sha256,
                        elapsed_ms=result.elapsed_ms, parse_ok=payload is not None,
                        n_rows=len(payload or []), archive_path=archive_path, error=error)

        if payload is None:
            note = error or "beranda tidak dapat diambil"
            store.finish_run(run_id, "failed", note=note)
            log.error("cuplikan %s gagal: %s", run_id, note)
            return {"run_id": run_id, "status": "failed", "note": note}

        rows = parse_home(payload)
        store.save_snapshot(
            rows, update_time if isinstance(update_time, str) else None, result.fetched_utc
        )

        now = now_utc()
        stale = [
            r for r in rows
            if r["observed_ts_utc"] is None
            or (now - r["observed_ts_utc"]) > dt.timedelta(hours=config.stale_alert_hours)
        ]
        status = "ok" if rows else "aborted"
        note = None if rows else "beranda mengembalikan nol stasiun"
        if not rows:
            log.error("cuplikan %s: portal mengembalikan nol stasiun", run_id)
        store.finish_run(run_id, status, stations_seen=len(rows), stations_ok=len(rows), note=note)
        return {
            "run_id": run_id,
            "status": status,
            "n_stations": len(rows),
            "update_time_wib": update_time,
            "stale": [r["initial"] for r in stale],
        }
    except Exception as exc:  # noqa: BLE001
        store.finish_run(run_id, "failed", note=f"{type(exc).__name__}: {exc}")
        log.exception("cuplikan %s gagal", run_id)
        return {"run_id": run_id, "status": "failed", "note": f"{type(exc).__name__}: {exc}"}
    finally:
        fetcher.close()
        store.close()
