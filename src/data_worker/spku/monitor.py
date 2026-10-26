"""Pemantauan kesehatan pengumpulan.

Kegagalan yang paling berbahaya bukan galat HTTP melainkan keberhasilan yang
kosong: peladen menjawab 200, halaman terbentuk, tetapi tidak ada datanya.
Karena itu pemantauan bertumpu pada kebasian data, bukan pada status
permintaan.
"""

from __future__ import annotations

import datetime as dt

from .normalize import UTC
from .store import Store, now_utc


def health(store: Store, stale_hours: float = 6.0, stale_ratio: float = 0.2) -> dict:
    conn = store.conn
    now = now_utc()

    last_full = conn.execute(
        "SELECT * FROM run WHERE kind='full' ORDER BY started_utc DESC LIMIT 1"
    ).fetchone()
    last_ok_full = conn.execute(
        "SELECT * FROM run WHERE kind='full' AND status IN ('ok','partial') "
        "ORDER BY started_utc DESC LIMIT 1"
    ).fetchone()

    active = conn.execute("SELECT COUNT(*) AS n FROM station WHERE active").fetchone()["n"]
    stale = store.stale_stations(stale_hours)
    # Kanal parameter dapat mati sendiri-sendiri sementara stasiunnya tetap
    # tampak sehat, jadi kebasian juga dinilai per parameter.
    stale_metric = [
        dict(r) for r in store.stale_metrics(stale_hours)
        if r["initial"] not in {x["initial"] for x in stale}
    ]
    gaps = store.metric_gaps()
    coverage = store.coverage()

    recent_aborts = conn.execute(
        "SELECT COUNT(*) AS n FROM run WHERE status='aborted' AND started_utc > %s",
        (now - dt.timedelta(days=1),),
    ).fetchone()["n"]

    revisions = conn.execute(
        "SELECT COUNT(*) AS n FROM observation_revision WHERE noticed_utc > %s",
        (now - dt.timedelta(days=1),),
    ).fetchone()["n"]

    alerts: list[str] = []
    if last_ok_full is None:
        alerts.append("belum pernah ada lintasan penuh yang berhasil")
    else:
        age = now - last_ok_full["started_utc"]
        if age > dt.timedelta(hours=12):
            alerts.append(f"lintasan penuh terakhir yang berhasil berumur {age}")
    if active and len(stale) > stale_ratio * active:
        alerts.append(
            f"{len(stale)} dari {active} stasiun tidak memperbarui data lebih dari {stale_hours} jam"
        )
    if recent_aborts:
        alerts.append(f"{recent_aborts} lintasan dibatalkan dalam 24 jam terakhir")
    if stale_metric:
        contoh = ", ".join(f"{r['initial']}/{r['metric']}" for r in stale_metric[:5])
        alerts.append(
            f"{len(stale_metric)} kanal parameter diam lebih dari {stale_hours} jam "
            f"pada stasiun yang selebihnya sehat: {contoh}"
        )

    return {
        "waktu": now.isoformat(timespec="seconds"),
        "lintasan_terakhir": dict(last_full) if last_full else None,
        "lintasan_berhasil_terakhir": dict(last_ok_full) if last_ok_full else None,
        "stasiun_aktif": active,
        "stasiun_basi": [dict(r) for r in stale],
        "parameter_basi": stale_metric,
        "lubang_parameter": gaps,
        "cakupan": coverage,
        "revisi_24jam": revisions,
        "peringatan": alerts,
    }


def format_health(report: dict) -> str:
    lines = [f"Kesehatan kolektor pada {report['waktu']}", ""]
    cov = report["cakupan"]
    lines.append(
        f"  Observasi tersimpan : {cov['n'] or 0:,} baris dari {cov['stations'] or 0} stasiun"
    )
    lines.append(f"  Rentang waktu      : {cov['t0']} s.d. {cov['t1']}")
    lines.append(f"  Stasiun aktif      : {report['stasiun_aktif']}")
    lines.append(f"  Stasiun basi       : {len(report['stasiun_basi'])}")
    lines.append(f"  Kanal parameter basi: {len(report.get('parameter_basi', []))}")
    lubang = report.get("lubang_parameter", [])
    lines.append(f"  Parameter berlubang : {len(lubang)}")
    for r in lubang[:5]:
        lines.append(
            f"      {r['initial']}/{r['metric']}: {r['lubang']} lubang, "
            f"terbesar {r['lubang_terbesar_menit']} menit (irama {r['irama_menit']} menit)"
        )
    lines.append(f"  Revisi nilai 24 jam: {report['revisi_24jam']}")
    last = report["lintasan_terakhir"]
    if last:
        lines.append(
            f"  Lintasan terakhir  : {last['run_id']} [{last['status']}] "
            f"{last.get('stations_ok')} stasiun, {last.get('rows_new')} baris baru"
        )
    if report["peringatan"]:
        lines.append("")
        lines.append("  PERINGATAN:")
        for item in report["peringatan"]:
            lines.append(f"    - {item}")
    else:
        lines.append("")
        lines.append("  Tidak ada peringatan.")
    return "\n".join(lines)
