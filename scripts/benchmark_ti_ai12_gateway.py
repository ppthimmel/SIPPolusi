"""Measure local gateway replay; never changes server data or registry aliases."""
import hashlib
import json
import time
import urllib.error
import urllib.request

url = 'http://gateway:8080/v1/routes'
base = {'origin': {'lon': 106.8227, 'lat': -6.1754},
        'destination': {'lon': 106.8456, 'lat': -6.2088}, 'mode': 'bike'}
out = []
for alpha in (0.0, 0.5, 1.0):
    for repeat in range(3):
        request = dict(base, preference={'alpha': alpha, 'beta': 1-alpha})
        req = urllib.request.Request(url, data=json.dumps(request).encode(), headers={'Content-Type': 'application/json'})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                body = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            body, status = error.read(), error.code
        elapsed = (time.perf_counter() - started) * 1000
        parsed = json.loads(body)
        out.append({'request': request, 'repeat': repeat, 'status': status, 'elapsed_ms': elapsed,
                    'response_sha256': hashlib.sha256(body).hexdigest(), 'metadata': parsed.get('metadata'),
                    'error': parsed.get('error'), 'feature_count': len(parsed.get('features', []))})
        print(f'alpha={alpha} repeat={repeat} status={status} elapsed_ms={elapsed:.1f}', flush=True)
with open('/output/gateway-results.json', 'w', encoding='utf-8') as stream:
    json.dump(out, stream, indent=2)
    stream.write('\n')
