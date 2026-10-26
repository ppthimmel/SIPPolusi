#!/bin/sh
# Gateway tests, run inside the image by the Dockerfile's `test` stage (so every
# image build runs them) or on demand:
#   docker build --target test src/gateway
#
# 1. 10-validate-env.sh accepts valid upstreams and rejects malformed ones
#    without printing the rejected value.
# 2. The real entrypoint renders the template and `nginx -t` accepts it.
# 3. With a stub upstream, only the public API reaches the Cache API; every
#    other path is answered 404 by the gateway.
# 4. Rate limiting per client address answers 429 rate_limited with Retry-After.
# 5. An upstream that is down or does not answer gives 502 or 504 in the
#    uniform error schema (the 504 case waits out the 10 s read timeout).
set -u

VALIDATE=/docker-entrypoint.d/10-validate-env.sh
failures=0

pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; failures=$((failures + 1)); }

# expect_valid LABEL CACHE_API_UPSTREAM [PORT]
expect_valid() {
    if out=$(CACHE_API_UPSTREAM="$2" PORT="${3-8080}" sh "$VALIDATE" 2>&1); then
        pass "accepts $1"
    else
        fail "accepts $1: $out"
    fi
}

# expect_invalid LABEL CACHE_API_UPSTREAM [PORT]
# A rejected upstream is never printed, since it may hold a credential: cases
# that carry the marker $SECRET check that it does not reach the output.
SECRET=s3cret
expect_invalid() {
    if out=$(CACHE_API_UPSTREAM="$2" PORT="${3-8080}" sh "$VALIDATE" 2>&1); then
        fail "rejects $1: accepted"
        return
    fi
    case "$out" in
        *"gateway: invalid configuration:"* ) ;;
        * ) fail "rejects $1: no actionable message: $out"; return ;;
    esac
    case "$out" in
        *"$SECRET"* ) fail "rejects $1: rejected value printed: $out"; return ;;
    esac
    pass "rejects $1"
}

echo "== 1. environment validation"
expect_valid "compose service name" "http://cache_api:8000"
expect_valid "Railway private domain" "http://cache-api.railway.internal:8000"
expect_valid "https public domain with port" "https://cache-api-staging.up.railway.app:443"
expect_valid "IPv4 address" "http://127.0.0.1:8000"
expect_valid "bracketed IPv6 address" "http://[fd12:3456:789a::1]:8000"
expect_valid "highest port" "http://cache_api:65535"
if out=$(env -u BACKEND_UPSTREAM CACHE_API_UPSTREAM=http://cache_api:8000 PORT=8080 sh "$VALIDATE" 2>&1); then
    pass "accepts a missing BACKEND_UPSTREAM (no longer used)"
else
    fail "BACKEND_UPSTREAM still required: $out"
fi

expect_invalid "empty CACHE_API_UPSTREAM" ""
expect_invalid "missing scheme" "cache_api:8000"
expect_invalid "unsupported scheme" "ftp://cache_api:8000"
expect_invalid "scheme only" "http://"
expect_invalid "missing port" "http://cache-api.railway.internal"
expect_invalid "empty port" "http://cache_api:"
expect_invalid "non-numeric port" "http://cache_api:abc"
expect_invalid "port 0" "http://cache_api:0"
expect_invalid "port above 65535" "http://cache_api:99999"
expect_invalid "trailing slash" "http://cache_api:8000/"
expect_invalid "path" "http://cache_api:8000/v1/$SECRET"
expect_invalid "query" "http://cache_api:8000?token=$SECRET"
expect_invalid "fragment" "http://cache_api:8000#$SECRET"
expect_invalid "user-info with password" "http://user:$SECRET@cache_api:8000"
expect_invalid "semicolon (nginx directive)" "http://cache_api:8000; return 200 $SECRET"
expect_invalid "bare semicolon" "http://cache_api:8000;"
expect_invalid "brace" "http://cache_api:8000}"
expect_invalid "double quote" 'http://cache_api:8000"'
expect_invalid "single quote" "http://cache_api:8000'"
expect_invalid "nginx variable" 'http://cache_api:8000$args'
expect_invalid "space" "http://cache api:8000 $SECRET"
expect_invalid "tab" "$(printf 'http://cache_api:8000\t%s' "$SECRET")"
expect_invalid "line break" "$(printf 'http://cache_api:8000\nhttp://%s:1' "$SECRET")"
expect_invalid "unbracketed IPv6" "http://fd12::1:8000"
expect_invalid "unclosed IPv6 bracket" "http://[fd12::1:8000"
expect_invalid "non-numeric PORT" "http://cache_api:8000" "80a"
expect_invalid "PORT 0" "http://cache_api:8000" "0"

echo "== 2. template render and nginx -t (real entrypoint)"
export PORT=8080 CACHE_API_UPSTREAM=http://127.0.0.1:9002
if out=$(/docker-entrypoint.sh nginx -t 2>&1); then
    pass "entrypoint renders template, nginx -t ok"
else
    fail "entrypoint/nginx -t: $out"
    echo "$failures failure(s)"
    exit 1
fi

# Every request below comes from 127.0.0.1, so each one claims its own client
# address in X-Real-IP (trusted, see the template) to stay clear of the rate
# limits; section 4 reuses one address on purpose. A file holds the counter
# because most calls run in a $(...) subshell.
echo 0 > /tmp/client_n
client_ip() {
    n=$(($(cat /tmp/client_n) + 1))
    echo "$n" > /tmp/client_n
    echo "10.$((n / 250)).$((n % 250)).1"
}

echo "== 3. routing with a stub upstream"
cp /tests/stubs.conf /etc/nginx/conf.d/zz-test-stubs.conf
# A copy of the error log (warn and above, like stderr) for the checks on what
# NGINX itself writes there.
ERROR_LOG=/tmp/gateway-error.log
nginx -g "error_log $ERROR_LOG warn;"
for _ in 1 2 3 4 5 6 7 8 9 10; do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/healthz" && break
    sleep 0.2
done

# expect_route METHOD PATH STATUS BODY_PREFIX [curl args...]
expect_route() {
    method=$1 path=$2 want_status=$3 want_body=$4
    shift 4
    status=$(curl -s -X "$method" -o /tmp/body -D /tmp/headers -w '%{http_code}' \
        -H "X-Real-IP: $(client_ip)" "$@" "http://127.0.0.1:$PORT$path")
    body=$(cat /tmp/body)
    request_id=$(grep -i '^x-request-id:' /tmp/headers | tr -d '\r' | cut -d' ' -f2)
    id_headers=$(grep -ci '^x-request-id:' /tmp/headers)
    if [ "$id_headers" != 1 ]; then
        fail "$method $path: $id_headers X-Request-ID headers, want exactly 1"
    elif [ "$status" != "$want_status" ]; then
        fail "$method $path: HTTP $status, want $want_status ($body)"
    elif [ "${body#"$want_body"}" = "$body" ]; then
        fail "$method $path: body '$body', want prefix '$want_body'"
    elif [ -z "$request_id" ]; then
        fail "$method $path: no X-Request-ID header"
    else
        case "$want_body" in
            # Proxied: the upstream must have received the same request ID.
            cache_api*)
                [ "${body##* }" = "$request_id" ] || { fail "$method $path: upstream got request ID '${body##* }', response has '$request_id'"; return; } ;;
        esac
        pass "$method $path -> HTTP $status $(printf '%s' "$body" | cut -c1-60)"
    fi
}

NOT_FOUND='{"error":{"code":"not_found"'

# The public API: exact path, query string unchanged.
expect_route GET  /healthz                       200 '{"status":"ok","service":"gateway"}'
expect_route POST /v1/routes                     200 'cache_api /v1/routes '
expect_route GET  '/v1/exposure-surface?bbox=106.8,-6.2,106.81,-6.19&zoom=15' 200 'cache_api /v1/exposure-surface?bbox=106.8,-6.2,106.81,-6.19&zoom=15 '
# A raw path that NGINX normalises to a public path reaches the upstream as
# that public path, never in its raw form.
expect_route POST '/v1/osm/version/../../routes' 200 'cache_api /v1/routes ' --path-as-is
expect_route GET  '/v1/osm/x/%2E%2E/%2E%2E/exposure-surface?zoom=1' 200 'cache_api /v1/exposure-surface?zoom=1 ' --path-as-is

# Everything else is closed: Cache API inspection and internal reads, the
# backend, health endpoints, and near misses of the public paths.
for path in /v1/routes/solve /v1/routes/plan /v1/routes/solver-input /v1/routes/ \
            /v1/exposure-surface/ /v1/osm/version '/v1/osm/graph?mode=walk' \
            /v1/osm/subgraph /v1/pollution/windows /v1/pollution/edge-weights \
            /v1/other /v1/ /v1 /health /health/ready /metrics /docs /openapi.json /; do
    expect_route GET "$path" 404 "$NOT_FOUND"
done
expect_route POST /v1/routes/solve 404 "$NOT_FOUND"
expect_route POST /v1/routes%2Fsolve 404 "$NOT_FOUND"
expect_route GET  /v1/routes/../osm/version 404 "$NOT_FOUND" --path-as-is

# The upstream receives when the gateway got the request (epoch seconds with ms).
received_at=$(curl -s -X POST -o /dev/null -D - -H "X-Real-IP: $(client_ip)" "http://127.0.0.1:$PORT/v1/routes" | grep -i '^x-seen-received-at:' | tr -d '\r' | cut -d' ' -f2)
case "$received_at" in
    [0-9]*.[0-9][0-9][0-9]) pass "X-Request-Received-At forwarded ($received_at)" ;;
    *) fail "X-Request-Received-At not forwarded: '$received_at'" ;;
esac

# expect_json_error METHOD PATH STATUS CODE [curl args...]: NGINX's own errors use
# the uniform schema. CLIENT=<address> sends as that client instead of a new one.
expect_json_error() {
    method=$1 path=$2 want_status=$3 want_code=$4
    shift 4
    status=$(curl -s -X "$method" -o /tmp/body -D /tmp/headers -w '%{http_code}' \
        -H "X-Real-IP: ${CLIENT:-$(client_ip)}" "$@" "http://127.0.0.1:$PORT$path")
    body=$(cat /tmp/body)
    request_id=$(grep -i '^x-request-id:' /tmp/headers | tr -d '\r' | cut -d' ' -f2)
    id_headers=$(grep -ci '^x-request-id:' /tmp/headers)
    content_type=$(grep -i '^content-type:' /tmp/headers | tr -d '\r' | cut -d' ' -f2)
    case "$body" in
        "{\"error\":{\"code\":\"$want_code\","*"\"request_id\":\"$request_id\"}}") ok=1 ;;
        *) ok=0 ;;
    esac
    if [ "$status" != "$want_status" ] || [ "$ok" != 1 ] || [ "$content_type" != "application/json" ] \
            || [ -z "$request_id" ] || [ "$id_headers" != 1 ]; then
        fail "$method $path: HTTP $status $content_type '$body' ($id_headers X-Request-ID), want $want_status $want_code"
    else
        pass "$method $path -> HTTP $status $want_code (JSON)"
    fi
}

head -c 20000 /dev/zero | tr '\0' 'x' > /tmp/big_body
head -c 16000 /dev/zero | tr '\0' 'x' > /tmp/ok_body
expect_json_error POST /v1/routes 413 payload_too_large --data-binary @/tmp/big_body -H 'Content-Type: application/json'
status=$(curl -s -o /dev/null -w '%{http_code}' -H "X-Real-IP: $(client_ip)" --data-binary @/tmp/ok_body "http://127.0.0.1:$PORT/v1/routes")
[ "$status" = 200 ] && pass "POST /v1/routes with 16 000 bytes reaches the Cache API" || fail "16 000-byte body: HTTP $status"
if grep -q "buffered to a temporary file" "$ERROR_LOG"; then
    fail "an accepted /v1/routes body was spilled to disk and logged: $(grep 'temporary file' "$ERROR_LOG")"
else
    pass "accepted /v1/routes bodies stay in memory (no temp-file warning with the client address)"
fi

echo "== 4. rate limiting (per client address)"
# status_of PATH CLIENT_IP [curl args...]: HTTP status of one request.
status_of() {
    path=$1 ip=$2
    shift 2
    curl -s -o /dev/null -w '%{http_code}' -H "X-Real-IP: $ip" "$@" "http://127.0.0.1:$PORT$path"
}

# expect_burst PATH CLIENT_IP ALLOWED [curl args...]: ALLOWED quick requests
# pass, the next one is refused.
expect_burst() {
    path=$1 ip=$2 allowed=$3
    shift 3
    got=""
    i=0
    while [ "$i" -lt "$allowed" ]; do
        got="$got $(status_of "$path" "$ip" "$@")"
        i=$((i + 1))
    done
    want=$(i=0; while [ "$i" -lt "$allowed" ]; do printf ' 200'; i=$((i + 1)); done)
    if [ "$got" = "$want" ]; then
        pass "$path: $allowed quick requests from $ip pass (rate + burst)"
    else
        fail "$path: first $allowed quick requests from $ip got$got, want$want"
    fi
}

# expect_retry_after SECONDS: the last response (expect_json_error's) carries it.
expect_retry_after() {
    retry_after=$(grep -i '^retry-after:' /tmp/headers | tr -d '\r' | cut -d' ' -f2)
    case "$(cat /tmp/body)" in
        *"dalam $1 detik"*) in_message=1 ;;
        *) in_message=0 ;;
    esac
    if [ "$retry_after" = "$1" ] && [ "$in_message" = 1 ]; then
        pass "429 carries Retry-After: $1 and the same wait in the message"
    else
        fail "429 Retry-After '$retry_after', message '$(cat /tmp/body)', want $1"
    fi
}

# POST /v1/routes: 20/min, burst 5, so 6 at once pass and the 7th is refused.
expect_burst /v1/routes 198.51.100.7 6 -X POST
CLIENT=198.51.100.7 expect_json_error POST /v1/routes 429 rate_limited
expect_retry_after 3
[ "$(status_of /v1/routes 198.51.100.8 -X POST)" = 200 ] \
    && pass "/v1/routes: another client address is not affected" \
    || fail "/v1/routes: another client address was refused too"
sleep 3
[ "$(status_of /v1/routes 198.51.100.7 -X POST)" = 200 ] \
    && pass "/v1/routes: the refused client passes again after Retry-After" \
    || fail "/v1/routes: still refused after waiting Retry-After"

# GET /v1/exposure-surface: 60/min, burst 10, so 11 at once pass.
expect_burst '/v1/exposure-surface?zoom=13' 198.51.100.9 11
CLIENT=198.51.100.9 expect_json_error GET '/v1/exposure-surface?zoom=13' 429 rate_limited
expect_retry_after 1

# The zones are separate: a client refused on one path still gets the other.
[ "$(status_of /v1/routes 198.51.100.9 -X POST)" = 200 ] \
    && pass "limits are per path: /v1/routes unaffected by the heatmap limit" \
    || fail "/v1/routes refused after the heatmap limit was hit"

# The gateway's health check is never limited.
got=$(i=0; while [ "$i" -lt 30 ]; do status_of /healthz 198.51.100.10; echo; i=$((i + 1)); done | sort -u | tr '\n' ' ')
[ "$got" = "200 " ] && pass "/healthz: 30 quick requests all 200 (not limited)" || fail "/healthz under load: $got"

# Rejections are logged at info, below the error log's threshold, so a refused
# client's address does not reach it (the access log still records each 429).
if grep -q "limiting requests" "$ERROR_LOG"; then
    fail "rate-limit rejections reach the error log: $(grep 'limiting requests' "$ERROR_LOG" | head -1)"
else
    pass "rate-limit rejections are not written to the error log"
fi

echo "== 5. upstream failures"
# Without the stub, the Cache API port refuses connections: 502.
rm -f /etc/nginx/conf.d/zz-test-stubs.conf
nginx -s reload
sleep 0.5
expect_json_error POST /v1/routes 502 upstream_unavailable
expect_json_error GET /v1/exposure-surface 502 upstream_unavailable

# An upstream that accepts the connection but never answers: 504 after the
# 10 s read timeout. nc keeps the connection open while its stdin is open.
(sleep 20 | nc -l -p 9002 > /dev/null 2>&1) &
hang_pid=$!
sleep 0.5
started=$(date +%s)
expect_json_error POST /v1/routes 504 upstream_timeout
elapsed=$(($(date +%s) - started))
if [ "$elapsed" -ge 9 ] && [ "$elapsed" -le 12 ]; then
    pass "504 after ${elapsed} s (proxy_read_timeout 10s)"
else
    fail "504 after ${elapsed} s, want about 10"
fi
kill "$hang_pid" 2>/dev/null
pkill -f "nc -l -p 9002" 2>/dev/null

# The gateway itself stays up while its upstream is down.
expect_route GET /healthz 200 '{"status":"ok","service":"gateway"}'

nginx -s quit
rm -f /etc/nginx/conf.d/default.conf

if [ "$failures" -gt 0 ]; then
    echo "$failures failure(s)"
    exit 1
fi
echo "all gateway tests passed"
