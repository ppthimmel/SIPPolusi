# API Gateway (NGINX custom)

Single public entry point for API requests (design document Table 3.2:
API Gateway → NGINX). Railway's edge terminates TLS and forwards to this
service; NGINX forwards `/v1/*` to the backend over Railway's private network.

| Path | Behaviour |
|---|---|
| `GET /healthz` | Gateway liveness, answered by NGINX itself (Railway health check). Never touches the backend. |
| `/v1/osm/*`, `/v1/pollution/*` | Proxied unchanged to `CACHE_API_UPSTREAM` (the Cache API, `src/backend/osm/Dockerfile`). |
| other `/v1/*` (e.g. `/v1/routes`) | Proxied unchanged to `BACKEND_UPSTREAM`. |
| anything else | `404` JSON error; internal endpoints such as the backend's `/health` are not exposed. |

All proxied paths share the same timeouts: connect 5 s, read/send 10 s
(Table 3.15). The frontend container forwards its `/v1/*` here, so this file
is the only place that maps paths to services.

Every response carries `X-Request-ID`; the same ID is sent to the backend and
written to the access log.

Rate limiting (429), the final upstream-failure responses, and the private-only
backend belong to TI-DO-05 and TI-DO-04; this service is their starting point.

## Configuration

| Variable | Required | Notes |
|---|---|---|
| `BACKEND_UPSTREAM` | yes | `scheme://host:port`, no path or trailing slash. Railway: `http://${{backend.RAILWAY_PRIVATE_DOMAIN}}:${{backend.PORT}}`; compose: `http://backend:8000` |
| `CACHE_API_UPSTREAM` | yes | Same format. Railway: `http://${{cache-api.RAILWAY_PRIVATE_DOMAIN}}:${{cache-api.PORT}}` (use the Cache API service's actual name); compose: `http://cache_api:8000` |
| `PORT` | no | Railway injects it; image default `8080` |

`docker-entrypoint.d/10-validate-env.sh` checks all of them before NGINX starts and
stops the container with a message naming the variable, e.g.

```
gateway: invalid configuration: BACKEND_UPSTREAM='http://backend:8000/' must not contain a path or trailing slash (nginx would replace the request path with it); use scheme://host:port
```

The template is rendered by the official image's entrypoint (envsubst limited
to `PORT`, `BACKEND_UPSTREAM`, `CACHE_API_UPSTREAM`, `NGINX_LOCAL_RESOLVERS`). The build renders it
with sample values and runs `nginx -t`, so a syntax error fails the build.

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
