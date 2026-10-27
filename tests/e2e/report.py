"""Render the E2E matrix and copy the run evidence into docs/ti-se-10/.

    python3 tests/e2e/report.py

Reads tests/e2e/out/ (written by run_e2e.py) and writes docs/ti-se-10/MATRIKS.md
plus docs/ti-se-10/hasil/ (results.json, versions.json, correlation.txt, worker
reports). Parquet artifacts stay in tests/e2e/out/ (not committed).
"""
from __future__ import annotations

import json
import pathlib
import shutil

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
DOCS = HERE.parents[1] / "docs" / "ti-se-10"


def cell(value) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= 220 else text[:217] + "…"


def main() -> None:
    results = json.loads((OUT / "results.json").read_text())
    versions = json.loads((OUT / "versions.json").read_text())
    s = results["summary"]
    lines = [
        "# Matriks uji end-to-end TI-SE-10",
        "",
        f"Dihasilkan oleh `tests/e2e/report.py` dari eksekusi {s['started_at']} – {s['finished_at']} (UTC): "
        f"**{s['passed']}/{s['checks']} pemeriksaan lulus**. Versi lengkap di [hasil/versions.json](hasil/versions.json).",
        "",
        f"- Commit: `{versions['git_commit']}` ({versions['git_branch']}{', dengan perubahan belum di-commit' if versions['git_dirty'] else ''})",
        f"- Dataset: `{versions['dataset']['dataset_version']}`, graf `{versions['dataset']['graph_version']}`, "
        f"ground truth {', '.join(versions['dataset']['ground_truth_windows'])}",
        f"- Basis data: {versions['postgres']}",
        "",
        "| # | Skenario | Pemeriksaan | Hasil | Bukti |",
        "|---|---|---|---|---|",
    ]
    for i, r in enumerate(results["results"], 1):
        lines.append(f"| {i} | {r['scenario']} | {cell(r['check'])} | {'✅ lulus' if r['passed'] else '❌ gagal'} | "
                     f"{cell(r['evidence'])} |")
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "MATRIKS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    hasil = DOCS / "hasil"
    hasil.mkdir(exist_ok=True)
    for name in ["results.json", "versions.json", "correlation.txt", *sorted(p.name for p in OUT.glob("worker-*.json"))]:
        shutil.copy2(OUT / name, hasil / name)
    print(f"{DOCS / 'MATRIKS.md'}: {s['passed']}/{s['checks']}")


if __name__ == "__main__":
    main()
