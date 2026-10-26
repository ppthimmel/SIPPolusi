# API Gateway (NGINX custom)

Single public entry point for API requests (design document Table 3.2:
API Gateway → NGINX). Railway's edge terminates TLS and forwards to this
service; NGINX forwards `/v1/*` to the backend over Railway's private network.

| Path | Behaviour |
|---|---|
| `GET /healthz` | Gateway liveness, answered by NGINX itself (Railway health check). Never touches the backend. |
| `/v1/osm/*`, `/v1/pollution/*` | Proxied unchanged to `CACHE_API_UPSTREAM` (the Cache API, `src/backend/osm/Dockerfile`). |
| `POST /v1/routes`, `GET /v1/exposure-surface` (exact paths) | Proxied unchanged to `CACHE_API_UPSTREAM` (public route planning and heatmap, TI-SE-08). The Cache API's `/v1/routes/*` inspection endpoints are not routed there. |
| other `/v1/*` | Proxied unchanged to `BACKEND_UPSTREAM`. |
| anything else | `404` JSON error; internal endpoints such as the backend's `/health` are not exposed. |

All proxied paths share the same timeouts: connect 5 s, read/send 10 s
(Table 3.15). The frontend container forwards its `/v1/*` here, so this file
is the only place that maps paths to services.

Errors produced by the gateway itself use the uniform schema
`{"error": {"code", "message", "request_id"}}` (design Table 3.16):

| Status | `code` | When |
|---|---|---|
| 404 | `not_found` | Path not exposed |
| 413 | `payload_too_large` | Body over 16 KiB on `POST /v1/routes` (other paths keep NGINX's 1 MiB default) |
| 502 | `upstream_unavailable` | The upstream cannot be reached |
| 504 | `upstream_timeout` | The upstream did not answer within 10 s |

Error responses from the upstreams themselves are passed through unchanged.
Rate limiting (429 `rate_limited`, Table 3.15) is not implemented yet.

Every response carries `X-Request-ID`; the same ID is sent to the backend and
written to the access log. Upstreams also receive `X-Request-Received-At` (epoch
seconds with millisecond precision, NGINX `$msec`), from which the Cache API
counts its 8 s latency budget (design Table 3.5).

Rate limiting (429), the final upstream-failure responses, and the private-only
backend belong to TI-DO-05 and TI-DO-04; this service is their starting point.

## Configuration

| Variable | Required | Notes |
|---|---|---|
| `BACKEND_UPSTREAM` | yes | `http(s)://host:port` and nothing else: port required (1–65535); no user-info, path, trailing slash, query or fragment. Host is a DNS name, IPv4, or bracketed IPv6 (`[fd12::1]`). Railway: `http://${{backend.RAILWAY_PRIVATE_DOMAIN}}:${{backend.PORT}}`; compose: `http://backend:8000` |
| `CACHE_API_UPSTREAM` | yes | Same format. Railway: `http://${{cache-api.RAILWAY_PRIVATE_DOMAIN}}:${{cache-api.PORT}}` (use the Cache API service's actual name); compose: `http://cache_api:8000` |
| `PORT` | no | Railway injects it; image default `8080` |

`docker-entrypoint.d/10-validate-env.sh` checks all of them before NGINX starts and
stops the container (exit 1) with a message naming the variable. A rejected
upstream value is never printed, since it may hold a pasted credential:

```
gateway: invalid configuration: BACKEND_UPSTREAM is not a valid upstream; expected scheme://host:port, e.g. http://backend.railway.internal:8000, with no user:password@, path, trailing slash, query or fragment (value not shown)
```

The template is rendered by the official image's entrypoint (envsubst limited
to `PORT`, `BACKEND_UPSTREAM`, `CACHE_API_UPSTREAM`, `NGINX_LOCAL_RESOLVERS`).

## Tests

`tests/run.sh` runs in the Dockerfile's `test` stage, and the default target
depends on it, so every build (including Railway's) runs it and a failure
fails the build. It needs no network and takes about a second:

1. the validator accepts valid upstreams (compose names, Railway private
   domains, IPv4, bracketed IPv6) and rejects malformed ones (missing or
   invalid port, user-info, path, query, fragment, whitespace, line breaks,
   `;`, quotes, `$`), without the rejected value reaching the output;
2. the real entrypoint renders the template and `nginx -t` accepts it;
3. against stub upstreams, `/healthz`, `/v1/routes` and other `/v1/*`,
   `/v1/osm/*`, `/v1/pollution/*` and unknown paths reach the intended place,
   with the request URI unchanged and the same `X-Request-ID` upstream and in
   the response.

```bash
docker build --target test src/gateway
```

## Logs

One JSON object per request on stdout, with a `level` Railway understands
(`error` for 5xx, `warn` for 4xx). Only method, path (no query string),
status, sizes, timings and the request ID are logged: no headers, bodies,
client address or user agent. NGINX's own `error_log` goes to stderr at
`warn` and above; its fixed format does include the client address and
request line (e.g. `backend could not be resolved ... client: 172.21.0.1`),
so it appears only for failures.

```json
{"time":"2026-10-09T07:40:01+00:00","level":"info","message":"POST /v1/routes 200","request_id":"4b0c…","method":"POST","path":"/v1/routes","status":200,"bytes_sent":112,"duration_s":0.004,"upstream_status":"200","upstream_duration_s":"0.004"}
```
