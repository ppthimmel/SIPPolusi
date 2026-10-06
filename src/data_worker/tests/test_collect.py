"""Uji lintasan penuh tanpa menyentuh jaringan."""

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from spku import collect as collect_mod  # noqa: E402
from spku.config import Config  # noqa: E402
from spku.fetch import FetchResult  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"

DETAIL_BY_UUID = {
    "2d554554-7567-4ac1-ab87-536260d2ba79": "detail_referensi_lengkap.html",
    "022441fc-0402-4b51-af62-ba5c8e4fa37f": "detail_sensor_meteo_nol.html",
    "42b53155-7cfe-4543-aec2-5832c886d36f": "detail_lcs_per_jam.html",
}


class FakeFetcher:
    """Peladen tiruan yang dapat disetel menjadi sehat, kosong, atau rusak."""

    def __init__(self, mode="ok", **_):
        self.mode = mode
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        text = self._body(path)
        return FetchResult(
            url=f"https://udara.jakarta.go.id{path}",
            status=200 if text is not None else 500,
            text=text,
            bytes=len(text or ""),
            sha256="x" * 64,
            elapsed_ms=5,
            fetched_utc=dt.datetime(2026, 9, 13, 7, 0, tzinfo=dt.timezone.utc),
            error=None if text is not None else "HTTP 500",
        )

    def _body(self, path):
        if path == "/lokasi-spku":
            return (FIX / "lokasi_spku.html").read_text(encoding="utf-8")
        if path == "/sitemap.xml":
            return (FIX / "sitemap.xml").read_text(encoding="utf-8")
        if path.startswith("/spku/"):
            station_uuid = path.rsplit("/", 1)[-1]
            if self.mode == "kosong":
                return _tanpa_observasi(station_uuid)
            if self.mode == "rusak":
                return (FIX / "detail_rusak.html").read_text(encoding="utf-8")
            return (FIX / DETAIL_BY_UUID[station_uuid]).read_text(encoding="utf-8")
        if path == "/":
            return (FIX / ("home_kosong.html" if self.mode == "kosong" else "home_ok.html")
                    ).read_text(encoding="utf-8")
        return None

    def close(self):
        self.closed = True

    def archive(self, *a, **k):
        return None

    def archive_raw(self, *a, **k):
        return None


def _tanpa_observasi(station_uuid):
    """Meniru gangguan 8 September 2026: HTTP 200, halaman utuh, nol data."""
    import json
    payload = {
        "datasourceID": station_uuid, "datasourceKode": None, "datasourceName": None,
        "type": None, "latitude": None, "longitude": None, "metrics": [],
        "rawHistory": [], "ispuHistory": [], "rawMeteoHistory": [],
        "weatherHistory": [], "dailyIspu30Days": [], "ispuValue": None,
    }
    return f"<html><script>const SPKU_DETAIL_DATA = {json.dumps(payload)};</script></html>"


def _config(db, **kw):
    return Config(database=db.dsn, db_schema=db.schema, archive_dir=None,
                  contact="uji@example.ac.id", delay_seconds=0, **kw)


def _patch(monkeypatch, mode):
    fake = FakeFetcher(mode)
    monkeypatch.setattr(collect_mod, "Fetcher", lambda *a, **k: fake)
    return fake


def test_lintasan_sehat_menulis_seluruh_stasiun(monkeypatch, db):
    _patch(monkeypatch, "ok")
    config = _config(db)
    summary = collect_mod.run_full(config)
    assert summary["status"] == "ok"
    assert summary["stations_ok"] == 3
    with db.store() as store:
        n = store.conn.execute("SELECT COUNT(*) AS n FROM observation").fetchone()["n"]
        stations = store.conn.execute("SELECT COUNT(*) AS n FROM station").fetchone()["n"]
    assert n == 6 * 96 + 6 * 96 + 48
    assert stations == 3


def test_lintasan_kedua_tidak_menggandakan_baris(monkeypatch, db):
    _patch(monkeypatch, "ok")
    config = _config(db)
    collect_mod.run_full(config)
    second = collect_mod.run_full(config)
    assert second["rows_new"] == 0
    assert second["rows_revised"] == 0


def test_muatan_kosong_membatalkan_lintasan_tanpa_menulis(monkeypatch, db):
    config = _config(db)
    _patch(monkeypatch, "ok")
    collect_mod.run_full(config)
    with db.store() as store:
        before = store.conn.execute("SELECT COUNT(*) AS n FROM observation").fetchone()["n"]

    _patch(monkeypatch, "kosong")
    summary = collect_mod.run_full(config)
    with db.store() as store:
        after = store.conn.execute("SELECT COUNT(*) AS n FROM observation").fetchone()["n"]
        aborted = store.conn.execute(
            "SELECT COUNT(*) AS n FROM run WHERE status='aborted'"
        ).fetchone()["n"]
    assert summary["status"] == "aborted"
    assert after == before, "lintasan kosong tidak boleh mengubah apa pun"
    assert aborted == 1


def test_halaman_rusak_tidak_menghapus_data_lama(monkeypatch, db):
    config = _config(db)
    _patch(monkeypatch, "ok")
    collect_mod.run_full(config)
    with db.store() as store:
        before = store.conn.execute("SELECT COUNT(*) AS n FROM observation").fetchone()["n"]

    _patch(monkeypatch, "rusak")
    summary = collect_mod.run_full(config)
    with db.store() as store:
        after = store.conn.execute("SELECT COUNT(*) AS n FROM observation").fetchone()["n"]
        failures = store.conn.execute(
            "SELECT COUNT(*) AS n FROM fetch_log WHERE NOT parse_ok AND error IS NOT NULL"
        ).fetchone()["n"]
    assert summary["status"] == "aborted"
    assert after == before
    assert failures >= 3


def test_stasiun_baru_tercatat_sebagai_peristiwa_daftar(monkeypatch, db):
    _patch(monkeypatch, "ok")
    config = _config(db)
    collect_mod.run_full(config)
    with db.store() as store:
        events = store.conn.execute(
            "SELECT event, COUNT(*) AS n FROM roster_event GROUP BY event"
        ).fetchall()
    assert dict((r["event"], r["n"]) for r in events) == {"appeared": 3}


def test_cuplikan_beranda_mendeteksi_portal_kosong(monkeypatch, db):
    config = _config(db)
    _patch(monkeypatch, "kosong")
    summary = collect_mod.run_snapshot(config)
    assert summary["status"] == "aborted"
    assert summary["n_stations"] == 0
