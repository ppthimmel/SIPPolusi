"""Zip a verified snapshot folder as the Colab data bundle (data only).

Usage: python make_bundle.py SNAPSHOT_DIR OUT_ZIP
The archive holds one top-level folder named after the snapshot version (e.g. ds-v0.4.0/).
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))


def main(snapshot: Path, out: Path) -> None:
    version = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))["version"]
    files = sorted(p for p in snapshot.rglob("*") if p.is_file())
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            z.write(p, f"{version}/{p.relative_to(snapshot).as_posix()}")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.with_suffix(".zip.sha256")).write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    print(f"{out} {out.stat().st_size / 1e6:.1f} MB, {len(files)} files, sha256 {digest}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
