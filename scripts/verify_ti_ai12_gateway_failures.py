"""Exercise gateway validation and unsupported-mode fallback in local replay."""
import json
import time
import urllib.error
import urllib.request

base = {'origin': {'lon': 106.8227, 'lat': -6.1754},
        'destination': {'lon': 106.8456, 'lat': -6.2088}}
cases = [('walk_fallback', dict(base, mode='walk', preference={'alpha': 0.5, 'beta': 0.5})),
         ('invalid_preference', dict(base, mode='bike', preference={'alpha': 0.8, 'beta': 0.8}))]
results = []
for name, body in cases:
    req = urllib.request.Request('http://gateway:8080/v1/routes', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(), error.code
    parsed = json.loads(raw)
    results.append({'case': name, 'request': body, 'status': status, 'elapsed_ms': (time.perf_counter()-start)*1000,
                    'metadata': parsed.get('metadata'), 'error': parsed.get('error'), 'detail': parsed.get('detail')})
    print(f'{name}: HTTP {status}', flush=True)
with open('/output/gateway-failures.json', 'w', encoding='utf-8') as stream:
    json.dump(results, stream, indent=2)
    stream.write('\n')
