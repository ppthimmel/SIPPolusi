"""Export the frozen spatial replay's public edge weights from local Postgres."""
import gzip
import hashlib
import subprocess
from pathlib import Path

out = Path('docs/experiments/TI-AI-12/inputs/spatial-replay-20261113.csv.gz')
if out.exists():
    raise FileExistsError(out)
out.parent.mkdir(parents=True, exist_ok=True)
sql = "COPY (SELECT edge_id,exposure_index FROM pollution.edge_pollution WHERE time_window_start = '2026-11-13 09:00:00+00' AND graph_version = 'jakarta-20261005' ORDER BY edge_id) TO STDOUT WITH CSV HEADER"
command = ['docker', 'exec', 'ti-ai11-verify-20261113-postgres-1', 'sh', '-c', 'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"', 'sh', sql]
data = subprocess.run(command, check=True, stdout=subprocess.PIPE).stdout
if len(data.splitlines()) != 372070 or not data.startswith(b'edge_id,exposure_index\n'):
    raise ValueError('Unexpected export row count or columns')
with out.open('wb') as raw:
    with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
        compressed.write(data)
print('rows=372069 csv_sha256=' + hashlib.sha256(data).hexdigest() + ' gzip_sha256=' + hashlib.sha256(out.read_bytes()).hexdigest())
