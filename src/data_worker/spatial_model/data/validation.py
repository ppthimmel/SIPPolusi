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
        # Frozen text may have been hashed with LF or CRLF, and Git can change
        # either on checkout. Preserve the frozen digest; binary inputs must
        # still match byte for byte.
        if actual != digest and path.suffix in (".md", ".json", ".toml"):
            lf = path.read_bytes().replace(b"\r\n", b"\n")
            for candidate in (lf, lf.replace(b"\n", b"\r\n")):
                actual = hashlib.sha256(candidate).hexdigest()
                if actual == digest:
                    break
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

