# API Gateway (NGINX custom)

Single public entry point for API requests (design document Table 3.2:
API Gateway → NGINX). Railway's edge terminates TLS and forwards to this
service; NGINX applies per-address rate limits and forwards the public API to
the Cache API over Railway's private network. Nothing else is exposed.

| Path | Behaviour |
|---|---|
| `GET /healthz` | Gateway liveness, answered by NGINX itself (Railway health check). Never touches an upstream; not rate limited. |
| `POST /v1/routes` (exact path) | Rate limited, then proxied to `CACHE_API_UPSTREAM` (IRouteQuery, TI-SE-08). Body at most 16 KiB. |
| `GET /v1/exposure-surface` (exact path) | Rate limited, then proxied to `CACHE_API_UPSTREAM` (IExposureSurface, TI-SE-08). |
| anything else | `404 not_found`, answered by the gateway. This includes the Cache API's `/v1/osm/*` and `/v1/pollution/*` reads, its `/v1/routes/*` inspection endpoints (`/solve`, `/plan`, `/solver-input`), `/docs`, every `/health*`, and the backend. |

Those two paths are the whole public contract (`src/backend/public_api`,
`openapi.json`); they are what the Frontend calls. Internal services reach the
Cache API and the backend directly over the private network (TI-DO-04).

The upstream receives the location's own path plus the query string unchanged,
not the raw request path: NGINX matches locations on the normalised path, so a
request for `/v1/osm/x/../../routes` reaches the Cache API as `/v1/routes`, never
in its raw form.

All proxied paths share the same timeouts: connect 5 s, read/send 10 s
(Table 3.15). The frontend container forwards its `/v1/*` here, so this file
is the only place that maps paths to services.

## Rate limiting

Per client address and per path (design Table 3.5 `enforce_rate_limit`,
Table 3.15, PNF-05). NGINX's `limit_req` with `nodelay` behaves as a token
bucket: the burst passes at once, then one request per refill interval.

| Path | Rate | Burst | Quick requests that pass | `Retry-After` |
|---|---|---|---|---|
| `POST /v1/routes` | 20 per minute (1 per 3 s) | 5 | 6 | `3` |
| `GET /v1/exposure-surface` | 60 per minute (1 per s) | 10 | 11 | `1` |

Table 3.15 gives no burst for the heatmap; 10 covers a few quick pans after the
Frontend's 500 ms debounce. A refused request gets `429 rate_limited` with
`Retry-After` set to one refill interval, the same wait in the message, and
never reaches the Cache API:

```
HTTP/1.1 429 Too Many Requests
Content-Type: application/json
Retry-After: 3
X-Request-ID: 8559dff2cf948f8b901bdbcb4e981d21

{"error":{"code":"rate_limited","message":"Terlalu banyak permintaan. Coba lagi dalam 3 detik.","request_id":"8559dff2cf948f8b901bdbcb4e981d21"}}
```

**Client address.** Railway's edge sets `X-Real-IP` to the caller's address on
every request and replaces any copy the caller sent; the Frontend passes it on
unchanged when it calls the gateway over the private network. The edge's own
addresses are not a documented range, so the gateway takes `X-Real-IP` from any
peer (`set_real_ip_from 0.0.0.0/0`). That is safe only because, on Railway,
nothing reaches the gateway except through the edge or the private network.
Locally anyone can set the header (the tests use this). Without the header the
TCP peer's address is used. **Do not put a CDN in front of the gateway without
revisiting this**: every user would share the CDN's address and one bucket.

## Errors

Errors produced by the gateway itself use the uniform schema
`{"error": {"code", "message", "request_id"}}` (design Table 3.16):

| Status | `code` | When |
|---|---|---|
| 404 | `not_found` | Path not exposed |
| 413 | `payload_too_large` | Body over 16 KiB on `POST /v1/routes` |
| 429 | `rate_limited` | Rate limit above exceeded; `Retry-After` in seconds |
| 502 | `upstream_unavailable` | The Cache API cannot be reached (refused, or its name does not resolve, e.g. while it redeploys) |
| 504 | `upstream_timeout` | The Cache API did not answer within 10 s |

Error responses from the Cache API itself (400, 422, 503, …) are passed through
unchanged. `/healthz` keeps answering 200 while the Cache API is down, so an
upstream outage never blocks a gateway deploy.

Every response carries exactly one `X-Request-ID`; the same ID is sent to the
upstream and written to the access log. Upstreams also receive
`X-Request-Received-At` (epoch seconds with millisecond precision, NGINX
`$msec`), from which the Cache API counts its 8 s latency budget (design
Table 3.5).

## Configuration

| Variable | Required | Notes |
|---|---|---|
| `CACHE_API_UPSTREAM` | yes | `http(s)://host:port` and nothing else: port required (1–65535); no user-info, path, trailing slash, query or fragment. Host is a DNS name, IPv4, or bracketed IPv6 (`[fd12::1]`). Railway: `http://${{cache-api.RAILWAY_PRIVATE_DOMAIN}}:${{cache-api.PORT}}` (use the Cache API service's actual name); compose: `http://cache_api:8000` |
| `PORT` | no | Railway injects it; image default `8080` |

`BACKEND_UPSTREAM` is no longer read: no public path goes to the backend. A
value left in the Railway service's variables is ignored and can be deleted.

`docker-entrypoint.d/10-validate-env.sh` checks the variables before NGINX starts and
stops the container (exit 1) with a message naming the variable. A rejected
upstream value is never printed, since it may hold a pasted credential:

```
gateway: invalid configuration: CACHE_API_UPSTREAM is not a valid upstream; expected scheme://host:port, e.g. http://cache-api.railway.internal:8000, with no user:password@, path, trailing slash, query or fragment (value not shown)
```

The template is rendered by the official image's entrypoint (envsubst limited
to `PORT`, `CACHE_API_UPSTREAM`, `NGINX_LOCAL_RESOLVERS`).

## Tests

`tests/run.sh` runs in the Dockerfile's `test` stage, and the default target
depends on it, so every build (including Railway's) runs it and a failure
fails the build. It needs no network and takes about 15 seconds, most of it the
504 case waiting out the real 10 s timeout:

1. the validator accepts valid upstreams (compose names, Railway private
   domains, IPv4, bracketed IPv6) and rejects malformed ones (missing or
   invalid port, user-info, path, query, fragment, whitespace, line breaks,
   `;`, quotes, `$`), without the rejected value reaching the output; a
   missing `BACKEND_UPSTREAM` is accepted;
2. the real entrypoint renders the template and `nginx -t` accepts it;
3. against a stub Cache API, `POST /v1/routes` and `GET /v1/exposure-surface`
   reach it with the query string unchanged and the same `X-Request-ID`
   upstream and in the response; raw paths with `..` or `%2E%2E` reach it only
   as the public path; every other path (the Cache API's internal and
   inspection endpoints, `/v1/*` near misses, health endpoints, `/docs`, `/`)
   gets the gateway's `404`; bodies over 16 KiB get `413`;
4. rate limiting: 6 quick `POST /v1/routes` from one address pass and the 7th
   gets `429 rate_limited` with `Retry-After: 3`; another address is not
   affected; the refused address passes again after 3 s; 11 quick heatmap
   requests pass and the 12th gets `429` with `Retry-After: 1`; the two limits
   are separate; 30 quick `/healthz` all get 200;
5. upstream failures: a refused connection gives `502 upstream_unavailable`; an
   upstream that accepts but never answers gives `504 upstream_timeout` after
   10 s; `/healthz` still answers 200.

```bash
docker build --target test src/gateway
```

Results, the same checks against the real Cache API in compose, and the
diagram of the public path: [`docs/devops/gateway-TI-DO-05.md`](../../docs/devops/gateway-TI-DO-05.md).

## Logs

One JSON object per request on stdout, with a `level` Railway understands
(`error` for 5xx, `warn` for 4xx, so each 429 is a warning). Only method, path
(no query string), status, sizes, timings and the request ID are logged: no
headers, bodies, client address or user agent. NGINX's own `error_log` goes to
stderr at `warn` and above. Its fixed format includes the client address and
request line, so it is kept for failures only (e.g. `cache_api could not be
resolved ... client: 203.0.113.7`); with `X-Real-IP` that address is now the
end user's, not the edge's. Rate-limit rejections are logged by NGINX at `info`,
below that threshold, so a refused client's address does not reach stderr.

```json
{"time":"2026-10-09T07:40:01+00:00","level":"info","message":"POST /v1/routes 200","request_id":"4b0c…","method":"POST","path":"/v1/routes","status":200,"bytes_sent":112,"duration_s":0.004,"upstream_status":"200","upstream_duration_s":"0.004"}
```
