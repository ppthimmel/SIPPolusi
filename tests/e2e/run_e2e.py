"""TI-SE-10 end-to-end test matrix: Data Worker -> model -> cache -> API -> frontend.

    docker compose -f tests/e2e/docker-compose.yml --profile tools build
    python3 tests/e2e/run_e2e.py

Standard library only. Drives the stack in tests/e2e/docker-compose.yml from a
fresh database, loads the controlled dataset (fixture/manifest.json), runs the
scenarios in order and writes:

    tests/e2e/out/results.json      every check with its evidence
    tests/e2e/out/correlation.txt   redacted log lines that tie requests and jobs together
    tests/e2e/out/versions.json     application, image, model and dataset versions

Exit code 0 when every check passed. A failed check is a finding for the defect
list, not a reason to stop: the run continues so the matrix is complete.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
COMPOSE = ["docker", "compose", "-f", str(HERE / "docker-compose.yml")]
GATEWAY = "http://127.0.0.1:18080"
FRONTEND = "http://127.0.0.1:18090"
CACHE_API = "http://127.0.0.1:18001"
STUB = "http://127.0.0.1:18100"

MONAS = {"lat": -6.1754, "lon": 106.8272}
BUNDARAN_HI = {"lat": -6.1950, "lon": 106.8230}
ROUTE = {"origin": MONAS, "destination": BUNDARAN_HI, "mode": "walk", "pollutant": "pm25",
         "preference": {"alpha": 0.3, "beta": 0.7}}
SURFACE = "/v1/exposure-surface?bbox=106.822,-6.180,106.832,-6.170&zoom=15"
W12, W13, W14, W15 = "2026-10-26T12:00Z", "2026-10-26T13:00Z", "2026-10-26T14:00Z", "2026-10-26T15:00Z"

RESULTS: list[dict] = []
SECRET_PATTERNS = [re.compile(r"postgres(ql)?://[^\s'\"]+"), re.compile(r"sipp:sipp"), re.compile(r"PASSWORD", re.I)]


# ------------------------------------------------------------------ helpers

def sh(*args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    import os
    return subprocess.run(list(args), capture_output=True, text=True, check=check,
                          env={**os.environ, **(env or {})})


def compose(*args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    return sh(*COMPOSE, *args, check=check, env=env)


def psql(query: str) -> list[list[str]]:
    out = compose("exec", "-T", "db", "psql", "-U", "sipp", "-d", "sipp", "-At", "-F", "\t", "-c", query).stdout
    return [line.split("\t") for line in out.strip().splitlines() if line]


def http(method: str, url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 20.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**({"Content-Type": "application/json"} if data else {}), **(headers or {})})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, hdrs, raw = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        status, hdrs, raw = err.code, err.headers, err.read()
    elapsed = time.perf_counter() - started
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = raw.decode(errors="replace")[:200]
    return {"status": status, "headers": {k.lower(): v for k, v in hdrs.items()}, "json": payload,
            "elapsed_s": round(elapsed, 3)}


def route(**over):
    return http("POST", GATEWAY + "/v1/routes", {**ROUTE, **over})


def check(scenario: str, name: str, ok: bool, evidence) -> bool:
    RESULTS.append({"scenario": scenario, "check": name, "passed": bool(ok), "evidence": evidence})
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return ok


def worker(window: str) -> tuple[int, dict | None]:
    """One Data Worker cycle for window; (exit code, JSON report)."""
    proc = compose("--profile", "tools", "run", "--rm", "worker", "python", "main.py", "cycle",
                   "--window-start", window, check=False)
    match = re.search(r"^\{.*^\}", proc.stdout, re.S | re.M)
    report = json.loads(match.group(0)) if match else None
    (OUT / f"worker-{window.replace(':', '')}.json").write_text(json.dumps(report, indent=2, default=str))
    return proc.returncode, report


def step(report: dict | None, name: str) -> dict:
    return next((s for s in (report or {}).get("steps", []) if s["step"] == name), {})


def error_of(resp) -> tuple:
    err = (resp["json"] or {}).get("error", {}) if isinstance(resp["json"], dict) else {}
    return resp["status"], err.get("code"), err.get("reason")


def no_false_success(scenario: str, resp) -> None:
    """AC: a failure never becomes zero exposure or a successful-looking route."""
    if resp["status"] != 200:
        check(scenario, "kegagalan tidak tampil sebagai sukses (status bukan 200, galat terstruktur)",
              isinstance(resp["json"], dict) and "error" in resp["json"], error_of(resp))
        return
    feats = resp["json"]["features"]
    exposures = [f["properties"]["exposure_index_min"] for f in feats]
    concentrations = [f["properties"]["exposure_ug_min_m3"] for f in feats]
    check(scenario, "tidak ada paparan nol pada rute 200",
          all(e > 0 for e in exposures) and all(c is None or c > 0 for c in concentrations),
          {"exposure_index_min": exposures, "exposure_ug_min_m3": concentrations})


def set_stub(mode: str) -> None:
    http("POST", f"{STUB}/__mode?name={mode}")


def restart_cache_api(**env) -> None:
    compose("up", "-d", "--force-recreate", "--wait", "cache_api", env=env)


def set_spku_portal(mode: str) -> None:
    compose("up", "-d", "--force-recreate", "spku_portal", env={"E2E_SPKU_MODE": mode})
    time.sleep(1)


# ------------------------------------------------------------------ scenarios

def scenario_empty_cache():
    s = "E2E-01 cache kosong"
    print(s)
    r = route()
    check(s, "POST /v1/routes -> 503 cache_unavailable/cache_empty", error_of(r) == (503, "cache_unavailable", "cache_empty"),
          error_of(r))
    no_false_success(s, r)
    sf = http("GET", GATEWAY + SURFACE)
    check(s, "GET /v1/exposure-surface -> 503 cache_empty", error_of(sf) == (503, "cache_unavailable", "cache_empty"),
          error_of(sf))
    health = http("GET", CACHE_API + "/health")["json"]
    check(s, "/health: database ok, belum ada time window", health["database"] == "ok" and
          health["pollution_time_window"] is None, health)
    ready = http("GET", CACHE_API + "/health/ready")
    check(s, "/health/ready: siap (graf ada) dengan polusi dilaporkan kosong",
          ready["status"] == 200 and (ready["json"] or {}).get("checks", {}).get("pollution") == "empty",
          {"status": ready["status"], "body": ready["json"]})


def scenario_success():
    s = "E2E-02 sukses Worker -> frontend"
    print(s)
    code, report = worker(W12)
    gt, inf = step(report, "fetch_ground_truth"), step(report, "trigger_downscale_inference")
    check(s, "siklus Worker berhasil (exit 0, tanpa langkah gagal)", code == 0 and report and not report["failed"],
          {"exit": code, "failed": (report or {}).get("failed"), "status": (report or {}).get("status")})
    check(s, "fetch_ground_truth menjelajahi portal (tiruan) dengan status ok",
          gt.get("status") == "ok" and gt.get("run_status") == "ok",
          {k: gt.get(k) for k in ("status", "run_status", "measurements", "stations", "failed_requests")})
    models = inf.get("model_metadata") or {}
    check(s, "inferensi menulis window complete; tiap polutan fallback IDW bertanda (Model Registry tidak dikonfigurasi)",
          inf.get("run_status") == "complete" and str(inf.get("model_version", "")).startswith("spatial-") and
          inf.get("estimation_source") == "idw" and inf.get("fallback_reason") and
          all(models.get(p, {}).get("source") == "idw" and models.get(p, {}).get("fallback_reason")
              for p in ("pm25", "no2")),
          {**{k: inf.get(k) for k in ("run_id", "run_status", "model_version", "rows_written", "coverage_ratio",
                                      "fallback_reason")},
           "model_metadata": {p: {k: v for k, v in m.items() if k in ("source", "model_version", "fallback_reason")}
                              for p, m in models.items()}})
    edges = int(psql("SELECT count(*) FROM osm.road_edge")[0][0])
    db = psql("SELECT status, model_version, coverage_ratio FROM pollution.pollution_window "
              "WHERE time_window_start = '2026-10-26 12:00+00'")
    check(s, "cache: window 12:00 complete, model sama dengan laporan Worker",
          db and db[0][0] == "complete" and db[0][1] == inf.get("model_version"), db)
    zeros = psql("SELECT count(*) FILTER (WHERE exposure_index = 0), count(*) FILTER (WHERE exposure_index IS NULL), "
                 "count(*) FILTER (WHERE pm25_ugm3 = 0 OR no2_ugm3 = 0), count(*) "
                 "FROM pollution.edge_pollution WHERE time_window_start = '2026-10-26 12:00+00'")[0]
    check(s, "cache: semua ruas punya estimasi, tidak ada nilai nol",
          zeros[0] == "0" and zeros[2] == "0" and int(zeros[3]) == edges, {"zero_index": zeros[0], "null_index": zeros[1],
                                                                          "zero_conc": zeros[2], "rows": zeros[3],
                                                                          "edges": edges})
    trace = psql("SELECT e.trace_run_id, count(*), bool_and(r.run_id IS NOT NULL) FROM pollution.edge_pollution e "
                 "LEFT JOIN pollution.inference_run r ON r.run_id = e.trace_run_id "
                 "WHERE e.time_window_start = '2026-10-26 12:00+00' GROUP BY 1")
    check(s, "cache: setiap baris menyimpan run_id Worker (trace_run_id -> inference_run)",
          len(trace) == 1 and trace[0][0] == inf.get("run_id") and trace[0][2] == "t",
          {"trace_run_id": [t[0] for t in trace], "worker_run_id": inf.get("run_id")})

    r = route()
    meta = (r["json"] or {}).get("metadata", {}) if r["status"] == 200 else {}
    active = psql("SELECT version FROM osm.graph_version WHERE status = 'active'")[0][0]
    check(s, "POST /v1/routes lewat gateway -> 200 GeoJSON", r["status"] == 200 and
          r["headers"].get("content-type") == "application/geo+json", {"status": r["status"], "elapsed_s": r["elapsed_s"]})
    check(s, "versi konsisten: time window, model downscaling, dan graf sama dengan cache",
          meta.get("time_window_start") == "2026-10-26T12:00:00Z" and
          meta.get("model_version", {}).get("downscaling") == inf.get("model_version") and
          meta.get("graph_version") == active,
          {k: meta.get(k) for k in ("time_window_start", "model_version", "graph_version", "route_source", "solver")})
    check(s, "rute dari model (stub IRouteSolve) dengan label policy", meta.get("route_source") == "policy" and
          meta.get("preference_applied") is True, {"route_source": meta.get("route_source")})
    fell_back = sorted(p for p, m in models.items() if m.get("fallback_reason"))
    check(s, "metadata.estimation menyebut sumber estimasi dan polutan fallback yang dicatat Worker",
          meta.get("estimation") == {"source": inf.get("estimation_source"), "fallback_pollutants": fell_back},
          {"estimation": meta.get("estimation"), "worker": {"estimation_source": inf.get("estimation_source"),
                                                            "fallback": fell_back}})
    no_false_success(s, r)
    rid = r["headers"].get("x-request-id")
    logs = compose("logs", "--no-color", "cache_api").stdout
    line = next((l for l in logs.splitlines() if f"request_id={rid}" in l and "route_inference" in l), None)
    gw = compose("logs", "--no-color", "gateway").stdout
    gw_line = next((l for l in gw.splitlines() if rid and rid in l), None)
    check(s, "korelasi: X-Request-ID = metadata.request_id = log gateway = log Cache API",
          rid and meta.get("request_id") == rid and line is not None and gw_line is not None,
          {"x_request_id": rid})
    record = log_json(line)
    check(s, "log Cache API berformat JSON dengan correlation_id = X-Request-ID",
          record.get("correlation_id") == rid and record.get("level") == "info",
          {k: record.get(k) for k in ("level", "logger", "correlation_id")})
    correlate(s, rid, line, gw_line, inf, trace[0][0] if trace else None)
    check(s, "missing_edge_count di respons sesuai cakupan Worker (100 %)", meta.get("missing_edge_count") == 0,
          {"missing_edge_count": meta.get("missing_edge_count"), "worker_coverage": inf.get("coverage_ratio")})

    sf = http("GET", GATEWAY + SURFACE)
    feats = (sf["json"] or {}).get("features", []) if sf["status"] == 200 else []
    check(s, "heatmap per ruas 200 dari window yang sama, tanpa nilai nol",
          sf["status"] == 200 and feats and all((f["properties"]["exposure_index"] or 0) > 0 for f in feats) and
          sf["json"]["metadata"]["time_window_start"] == "2026-10-26T12:00:00Z",
          {"status": sf["status"], "features": len(feats)})
    page = http("GET", FRONTEND + "/")
    via_fe = http("POST", FRONTEND + "/v1/routes", ROUTE)
    check(s, "frontend tersaji dan meneruskan /v1/* ke gateway", page["status"] == 200 and via_fe["status"] == 200,
          {"page": page["status"], "route_via_frontend": via_fe["status"]})


def scenario_next_window():
    s = "E2E-03 window baru dan idempotensi"
    print(s)
    old = http("GET", GATEWAY + SURFACE)
    code, report = worker(W13)
    check(s, "Worker menulis window 13:00", code == 0 and step(report, "trigger_downscale_inference").get("run_status")
          == "complete", step(report, "trigger_downscale_inference").get("run_id"))
    r = route()
    check(s, "API langsung memakai window terbaru 13:00",
          r["status"] == 200 and r["json"]["metadata"]["time_window_start"] == "2026-10-26T13:00:00Z",
          r["json"]["metadata"]["time_window_start"] if r["status"] == 200 else error_of(r))
    reval = http("GET", GATEWAY + SURFACE, headers={"If-None-Match": old["headers"].get("etag", "")})
    check(s, "heatmap: ETag window lama tidak lagi cocok (200 data baru, bukan 304)",
          reval["status"] == 200 and reval["json"]["metadata"]["time_window_start"] == "2026-10-26T13:00:00Z",
          {"status": reval["status"]})
    same = http("GET", GATEWAY + SURFACE, headers={"If-None-Match": reval["headers"].get("etag", "")})
    check(s, "heatmap: revalidasi tanpa perubahan -> 304", same["status"] == 304, {"status": same["status"]})
    code, report = worker(W13)
    check(s, "Worker idempoten: window 13:00 yang sudah complete dilewati",
          step(report, "trigger_downscale_inference").get("run_status") == "skipped",
          step(report, "trigger_downscale_inference").get("run_status"))


def scenario_source_unavailable():
    s = "E2E-04 sumber data tidak tersedia"
    print(s)
    set_spku_portal("down")
    code, report = worker(W15)  # portal down, no stored observation for 15:00
    set_spku_portal("ok")
    gt, inf = step(report, "fetch_ground_truth"), step(report, "trigger_downscale_inference")
    check(s, "portal SPKU mati: fetch_ground_truth berstatus failed, bukan ok", gt.get("status") == "failed" and
          gt.get("run_status") in ("aborted", "failed"),
          {k: gt.get(k) for k in ("status", "run_status", "failed_requests", "note")})
    check(s, "Worker menandai siklus gagal (exit 1) pada pengambilan data dan inferensi", code == 1 and
          {"fetch_ground_truth", "trigger_downscale_inference"} <= set((report or {}).get("failed", [])),
          {"exit": code, "failed": (report or {}).get("failed"), "inference": inf.get("error")})
    check(s, "tidak ada window 15:00 yang ditulis", not psql(
        "SELECT 1 FROM pollution.pollution_window WHERE time_window_start = '2026-10-26 15:00+00'"), None)
    r = route()
    check(s, "API tetap memakai window complete terakhir (13:00), bukan nilai kosong",
          r["status"] == 200 and r["json"]["metadata"]["time_window_start"] == "2026-10-26T13:00:00Z",
          r["json"]["metadata"]["time_window_start"] if r["status"] == 200 else error_of(r))
    no_false_success(s, r)
    stale_expected = dt.datetime.now(dt.timezone.utc) - dt.datetime(2026, 10, 26, 14, tzinfo=dt.timezone.utc) > \
        dt.timedelta(hours=3)
    check(s, "data_stale mengikuti umur window (> 3 jam setelah berakhir)",
          r["status"] == 200 and r["json"]["metadata"]["data_stale"] is stale_expected,
          {"data_stale": r["json"]["metadata"]["data_stale"] if r["status"] == 200 else None,
           "expected": stale_expected})


def scenario_window_being_written():
    s = "E2E-05 window sedang ditulis"
    print(s)
    psql("INSERT INTO pollution.pollution_window (time_window_start, pollutant, status, model_version) "
         "VALUES ('2026-10-26 14:00+00', 'pm25,no2', 'writing', 'spatial-e2e-writing')")
    r = route()
    check(s, "window berstatus writing tidak terbaca; API tetap 13:00",
          r["status"] == 200 and r["json"]["metadata"]["time_window_start"] == "2026-10-26T13:00:00Z",
          r["json"]["metadata"]["time_window_start"] if r["status"] == 200 else error_of(r))
    psql("DELETE FROM pollution.pollution_window WHERE time_window_start = '2026-10-26 14:00+00'")
    code, report = worker(W14)
    r = route()
    check(s, "setelah Worker selesai, window 14:00 langsung dipakai",
          code == 0 and r["status"] == 200 and r["json"]["metadata"]["time_window_start"] == "2026-10-26T14:00:00Z",
          r["json"]["metadata"]["time_window_start"] if r["status"] == 200 else error_of(r))


def scenario_model_failures():
    s = "E2E-06 inferensi rute gagal, fallback aktif"
    print(s)
    cases = [("slow", "model_timeout", "solver_timeout"), ("error", "model_error", "solver_error"),
             ("mislabel", "model_invalid_response", "invalid_solver_response"),
             ("no_route", "model_no_route", "solver_no_route")]
    for mode, fallback_reason, reason in cases:
        set_stub(mode)
        r = route()
        meta = r["json"]["metadata"] if r["status"] == 200 else {}
        check(s, f"model {mode}: 200 rute tercepat bertanda baseline, fallback_reason {fallback_reason}",
              r["status"] == 200 and meta.get("route_source") == "baseline" and meta.get("preference_applied") is False
              and meta.get("fallback_reason") == fallback_reason and r["elapsed_s"] < 10,
              {"status": r["status"], "fallback_reason": meta.get("fallback_reason"), "elapsed_s": r["elapsed_s"]})
        no_false_success(s, r)
        rid = r["headers"].get("x-request-id")
        line = next((l for l in compose("logs", "--no-color", "cache_api").stdout.splitlines()
                     if f"request_id={rid}" in l and "route_inference" in l), "")
        check(s, f"model {mode}: log mencatat outcome=fallback dan penyebab {reason}",
              "outcome=fallback" in line and f"reason={reason}" in line, redact(line[-260:]))
    set_stub("valid")
    compose("stop", "route_model")
    r = route()
    meta = r["json"]["metadata"] if r["status"] == 200 else {}
    check(s, "layanan model mati: fallback model_unavailable", r["status"] == 200 and
          meta.get("fallback_reason") == "model_unavailable", {"status": r["status"], "fallback_reason": meta.get("fallback_reason")})
    compose("start", "route_model")
    time.sleep(2)


def scenario_model_failures_no_fallback():
    s = "E2E-07 inferensi timeout, fallback mati"
    print(s)
    restart_cache_api(E2E_FALLBACK="none")
    set_stub("slow")
    r = route()
    check(s, "503 route_service_unavailable/solver_timeout dalam batas 10 s gateway",
          error_of(r) == (503, "route_service_unavailable", "solver_timeout") and r["elapsed_s"] < 10,
          {"error": error_of(r), "elapsed_s": r["elapsed_s"]})
    no_false_success(s, r)
    set_stub("no_route")
    r = route()
    check(s, "model menyatakan tidak ada rute: 503 solver_no_route, bukan 422",
          error_of(r) == (503, "route_service_unavailable", "solver_no_route"), error_of(r))
    set_stub("valid")
    restart_cache_api()


def scenario_api_failures():
    s = "E2E-08 API dan basis data gagal"
    print(s)
    compose("stop", "cache_api")
    r = route()
    check(s, "Cache API mati: gateway 502 upstream_unavailable (JSON seragam)",
          error_of(r)[:2] == (502, "upstream_unavailable"), error_of(r))
    fe = http("POST", FRONTEND + "/v1/routes", ROUTE)
    check(s, "lewat frontend: 502 yang sama diteruskan", error_of(fe)[:2] == (502, "upstream_unavailable"), error_of(fe))
    compose("start", "cache_api")
    compose("up", "-d", "--wait", "cache_api")
    compose("stop", "db")
    r = route()
    check(s, "basis data mati: 503 cache_unavailable/database_unavailable",
          error_of(r) == (503, "cache_unavailable", "database_unavailable"), error_of(r))
    health = http("GET", CACHE_API + "/health")
    check(s, "/health melaporkan degraded saat basis data mati",
          health["json"] and health["json"].get("status") == "degraded", health["json"])
    ready, live = http("GET", CACHE_API + "/health/ready"), http("GET", CACHE_API + "/health/live")
    check(s, "/health/ready 503 saat basis data mati, /health/live tetap 200",
          ready["status"] == 503 and (ready["json"] or {}).get("checks", {}).get("database") == "unavailable" and
          live["status"] == 200, {"ready": [ready["status"], ready["json"]], "live": live["status"]})
    no_false_success(s, r)
    compose("start", "db")
    compose("up", "-d", "--wait", "db")
    time.sleep(2)
    r = route()
    check(s, "pulih otomatis setelah basis data kembali", r["status"] == 200, {"status": r["status"]})


def scenario_version_mismatch():
    s = "E2E-09 versi graf dan cache tidak cocok"
    print(s)
    psql("UPDATE pollution.edge_pollution SET graph_version = 'jakarta-older'")
    r = route()
    check(s, "estimasi dari graf lain diabaikan: 503 cache_empty, bukan paparan nol",
          error_of(r) == (503, "cache_unavailable", "cache_empty"), error_of(r))
    no_false_success(s, r)
    psql("UPDATE pollution.edge_pollution SET graph_version = 'jakarta-20261005'")
    r = route()
    check(s, "kembali normal setelah versi cocok", r["status"] == 200, {"status": r["status"]})


def scenario_contract():
    s = "E2E-10 kontrak API publik lewat gateway"
    print(s)
    proc = compose("exec", "-T", "cache_api", "python", "-m", "public_api.smoke", "--base-url", "http://gateway:8080",
                   check=False)
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-300:]
    check(s, "koleksi uji (17 kasus: validasi, 413, 422, heatmap) lulus", proc.returncode == 0, summary)


def scenario_logs_safe():
    s = "E2E-11 log aman"
    print(s)
    logs = compose("logs", "--no-color").stdout
    hits = [p.pattern for p in SECRET_PATTERNS if p.search(logs)]
    check(s, "log semua layanan tanpa DSN/kata sandi", not hits, hits)
    coords = re.findall(r"-6\.1754|106\.8272", compose("logs", "--no-color", "cache_api", "gateway").stdout)
    check(s, "log Cache API dan gateway tanpa koordinat perjalanan (PNF-06)", not coords, len(coords))


# ------------------------------------------------------------------ evidence

CORRELATION: list[str] = []


def redact(text: str) -> str:
    for p in SECRET_PATTERNS:
        text = p.sub("<redacted>", text)
    return text


def log_json(line: str | None) -> dict:
    """The JSON record of one compose log line ("service-1  | {...}"), or {}."""
    try:
        record = json.loads((line or "").split("|", 1)[-1].strip())
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


def correlate(s, rid, api_line, gw_line, inference, trace_run_id):
    CORRELATION.extend([
        f"[{s}] Worker  run_id={inference.get('run_id')} model_version={inference.get('model_version')} "
        f"rows_written={inference.get('rows_written')} coverage={inference.get('coverage_ratio')}",
        f"[{s}] cache   edge_pollution.trace_run_id={trace_run_id}",
        f"[{s}] gateway {redact((gw_line or '').split('|', 1)[-1].strip())}",
        f"[{s}] API     {redact((api_line or '').split('|', 1)[-1].strip())}",
    ])


def versions() -> dict:
    def git(*args):
        return sh("git", "-C", str(ROOT), *args).stdout.strip()
    images = {}
    for service in ("db", "worker", "spku_portal", "route_model", "cache_api", "gateway", "frontend"):
        image = {"db": "postgis/postgis:18-3.6", "spku_portal": "python:3.11-slim"}.get(service, f"sippolusi-e2e-{service}")
        inspected = sh("docker", "image", "inspect", "--format", "{{.Id}}", image, check=False).stdout.strip()
        images[service] = {"image": image, "id": inspected[:19]}
    manifest = json.loads((HERE / "fixture" / "manifest.json").read_text())
    pg = psql("SELECT version()")
    return {
        "git_commit": git("rev-parse", "--short", "HEAD"),
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain")),
        "components": {name: git("log", "-1", "--format=%h %ad %s", "--date=short", "--", path)
                       for name, path in [("data_worker", "src/data_worker"), ("cache_api", "src/backend"),
                                          ("gateway", "src/gateway"), ("frontend", "src/frontend")]},
        "images": images,
        "dataset": {k: manifest[k] for k in ("dataset_version", "graph_version", "osm_timestamp", "bbox",
                                             "ground_truth_windows")},
        "postgres": pg[0][0][:60] if pg else None,
    }


def main() -> int:
    OUT.mkdir(exist_ok=True)
    (OUT / "artifacts").mkdir(exist_ok=True)
    print("== menyiapkan stack bersih")
    compose("down", "-v", "--remove-orphans", check=False)
    compose("up", "-d", "--wait", "db")
    print(compose("--profile", "tools", "run", "--rm", "fixture").stdout.strip().splitlines()[-1])
    compose("up", "-d", "--wait", "spku_portal", "route_model", "cache_api", "gateway", "frontend")
    started = dt.datetime.now(dt.timezone.utc)

    for scenario in (scenario_empty_cache, scenario_success, scenario_next_window, scenario_source_unavailable,
                     scenario_window_being_written, scenario_model_failures, scenario_model_failures_no_fallback,
                     scenario_api_failures, scenario_version_mismatch, scenario_contract, scenario_logs_safe):
        try:
            scenario()
        except Exception as exc:  # noqa: BLE001 - record and keep going so the matrix is complete
            check(scenario.__name__, "skenario selesai tanpa galat runner", False, f"{type(exc).__name__}: {exc}")

    passed = sum(r["passed"] for r in RESULTS)
    summary = {"started_at": started.isoformat(timespec="seconds"),
               "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "checks": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed}
    (OUT / "versions.json").write_text(json.dumps(versions(), indent=2, default=str))
    (OUT / "results.json").write_text(json.dumps({"summary": summary, "results": RESULTS}, indent=2, default=str))
    (OUT / "correlation.txt").write_text("\n".join(CORRELATION) + "\n")
    print(f"== {passed}/{len(RESULTS)} pemeriksaan lulus; hasil di {OUT.relative_to(ROOT)}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
