"""Frozen snapshot integrity across Git text checkout conventions."""
import hashlib
from pathlib import Path
import shutil

import pytest

from spatial_model.data.validation import verify_snapshot

SNAPSHOT = Path(__file__).resolve().parents[3] / "docs/experiments/ds-v0.3.0"
TEXT_SUFFIXES = {".md", ".json", ".toml"}


@pytest.mark.parametrize("frozen_eol", [b"\n", b"\r\n"])
@pytest.mark.parametrize("checkout_eol", [b"\n", b"\r\n"])
def test_frozen_text_hashes_survive_git_checkout(tmp_path, frozen_eol, checkout_eol):
    root = tmp_path / "snapshot"
    shutil.copytree(SNAPSHOT, root)
    frozen_hashes = {}
    for line in (root / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        path = root / name
        if path.suffix in TEXT_SUFFIXES:
            lf = path.read_bytes().replace(b"\r\n", b"\n")
            digest = hashlib.sha256(lf.replace(b"\n", frozen_eol)).hexdigest()
            path.write_bytes(lf.replace(b"\n", checkout_eol))
        frozen_hashes[name] = digest
    (root / "CHECKSUMS.sha256").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in frozen_hashes.items()), encoding="utf-8")
    assert verify_snapshot(root) == frozen_hashes


@pytest.mark.parametrize("name", ["README.md", "manifest.json", "config.toml", "labels.parquet"])
def test_checkout_normalization_still_rejects_modified_content(tmp_path, name):
    root = tmp_path / "snapshot"
    shutil.copytree(SNAPSHOT, root)
    path = root / name
    path.write_bytes(path.read_bytes() + b"changed\n")
    with pytest.raises(ValueError, match=f"Snapshot checksum mismatch: {name}"):
        verify_snapshot(root)


def test_binary_line_endings_are_not_normalized(tmp_path):
    root = tmp_path / "snapshot"
    shutil.copytree(SNAPSHOT, root)
    path = root / "labels.parquet"
    original = b"binary\r\ncontent\r\n"
    checksums = (root / "CHECKSUMS.sha256").read_text(encoding="utf-8")
    checksums = "\n".join(
        f"{hashlib.sha256(original).hexdigest()}  labels.parquet" if line.split(maxsplit=1)[1] == "labels.parquet" else line
        for line in checksums.splitlines()) + "\n"
    (root / "CHECKSUMS.sha256").write_text(checksums, encoding="utf-8")
    path.write_bytes(original.replace(b"\r\n", b"\n"))
    with pytest.raises(ValueError, match="Snapshot checksum mismatch: labels.parquet"):
        verify_snapshot(root)
