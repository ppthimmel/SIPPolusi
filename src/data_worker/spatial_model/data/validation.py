"""Frozen snapshot integrity; text checkout conversion only."""
import hashlib
from pathlib import Path

def stgnn_hash(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            result.update(block)
    return result.hexdigest()

def verify_snapshot(root):
    """Verify the frozen checksum list before reading training inputs."""
    root = Path(root).resolve()
    hashes = {}
    for line in (root / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Snapshot path outside root: {name}")
        actual = stgnn_hash(path)
        # Git may check text out as CRLF on Windows. Accept only this text-only
        # conversion; Parquet and other binary inputs must match byte for byte.
        if actual != digest and path.suffix in (".md", ".json", ".toml"):
            actual = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if actual != digest:
            raise ValueError(f"Snapshot checksum mismatch: {name}")
        hashes[name] = digest
    required = {"labels.parquet", "stations.parquet", "split_spec.json", "stgnn/features.parquet",
                "stgnn/nodes.parquet", "stgnn/edges.parquet", "stgnn/manifest.json",
                "splits/train.parquet", "splits/val.parquet", "splits/test.parquet",
                "splits/loso_folds.parquet"}
    if not required <= hashes.keys():
        raise ValueError("Incomplete frozen snapshot checksum list")
    return hashes

