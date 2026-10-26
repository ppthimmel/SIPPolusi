#!/bin/sh
# Gateway tests, run inside the image by the Dockerfile's `test` stage (so every
# image build runs them) or on demand:
#   docker build --target test src/gateway
#
# 1. 10-validate-env.sh accepts valid upstreams and rejects malformed ones
#    without printing the rejected value.
# 2. The real entrypoint renders the template and `nginx -t` accepts it.
# 3. With stub upstreams, each path category reaches the intended upstream.
set -u

VALIDATE=/docker-entrypoint.d/10-validate-env.sh
GOOD_CACHE=http://cache_api:8000
failures=0

pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; failures=$((failures + 1)); }

# expect_valid LABEL BACKEND_UPSTREAM [CACHE_API_UPSTREAM] [PORT]
expect_valid() {
    if out=$(BACKEND_UPSTREAM="$2" CACHE_API_UPSTREAM="${3-$GOOD_CACHE}" PORT="${4-8080}" sh "$VALIDATE" 2>&1); then
        pass "accepts $1"
    else
        fail "accepts $1: $out"
    fi
}

# expect_invalid LABEL BACKEND_UPSTREAM [CACHE_API_UPSTREAM] [PORT]
# A rejected upstream is never printed, since it may hold a credential: cases
# that carry the marker $SECRET check that it does not reach the output.
SECRET=s3cret
expect_invalid() {
    if out=$(BACKEND_UPSTREAM="$2" CACHE_API_UPSTREAM="${3-$GOOD_CACHE}" PORT="${4-8080}" sh "$VALIDATE" 2>&1); then
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
expect_valid "compose service name" "http://backend:8000"
expect_valid "underscore in compose name" "http://backend:8000" "http://cache_api:8000"
expect_valid "Railway private domain" "http://backend.railway.internal:8000"
expect_valid "https public domain with port" "https://backend-staging.up.railway.app:443"
expect_valid "IPv4 address" "http://127.0.0.1:8000"
expect_valid "bracketed IPv6 address" "http://[fd12:3456:789a::1]:8000"
expect_valid "highest port" "http://backend:65535"

expect_invalid "empty BACKEND_UPSTREAM" ""
expect_invalid "empty CACHE_API_UPSTREAM" "http://backend:8000" ""
expect_invalid "missing scheme" "backend:8000"
expect_invalid "unsupported scheme" "ftp://backend:8000"
expect_invalid "scheme only" "http://"
expect_invalid "missing port" "http://backend.railway.internal"
expect_invalid "empty port" "http://backend:"
expect_invalid "non-numeric port" "http://backend:abc"
expect_invalid "port 0" "http://backend:0"
expect_invalid "port above 65535" "http://backend:99999"
expect_invalid "trailing slash" "http://backend:8000/"
expect_invalid "path" "http://backend:8000/v1/$SECRET"
expect_invalid "query" "http://backend:8000?token=$SECRET"
expect_invalid "fragment" "http://backend:8000#$SECRET"
expect_invalid "user-info with password" "http://user:$SECRET@backend:8000"
expect_invalid "user-info in cache upstream" "http://backend:8000" "http://user:$SECRET@cache_api:8000"
expect_invalid "semicolon (nginx directive)" "http://backend:8000; return 200 $SECRET"
expect_invalid "bare semicolon" "http://backend:8000;"
expect_invalid "brace" "http://backend:8000}"
expect_invalid "double quote" 'http://backend:8000"'
expect_invalid "single quote" "http://backend:8000'"
expect_invalid "nginx variable" 'http://backend:8000$args'
expect_invalid "space" "http://back end:8000 $SECRET"
expect_invalid "tab" "$(printf 'http://backend:8000\t%s' "$SECRET")"
expect_invalid "line break" "$(printf 'http://backend:8000\nhttp://%s:1' "$SECRET")"
expect_invalid "unbracketed IPv6" "http://fd12::1:8000"
expect_invalid "unclosed IPv6 bracket" "http://[fd12::1:8000"
expect_invalid "malformed cache upstream" "http://backend:8000" "http://cache_api:8000/v1"
expect_invalid "non-numeric PORT" "http://backend:8000" "$GOOD_CACHE" "80a"
expect_invalid "PORT 0" "http://backend:8000" "$GOOD_CACHE" "0"

echo "== 2. template render and nginx -t (real entrypoint)"
export PORT=8080 BACKEND_UPSTREAM=http://127.0.0.1:9001 CACHE_API_UPSTREAM=http://127.0.0.1:9002
if out=$(/docker-entrypoint.sh nginx -t 2>&1); then
    pass "entrypoint renders template, nginx -t ok"
else
    fail "entrypoint/nginx -t: $out"
    echo "$failures failure(s)"
    exit 1
fi

echo "== 3. routing with stub upstreams"
cp /tests/stubs.conf /etc/nginx/conf.d/zz-test-stubs.conf
nginx
for _ in 1 2 3 4 5 6 7 8 9 10; do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/healthz" && break
    sleep 0.2
done

# expect_route METHOD PATH STATUS BODY_PREFIX
expect_route() {
    status=$(curl -s -X "$1" -o /tmp/body -D /tmp/headers -w '%{http_code}' "http://127.0.0.1:$PORT$2")
    body=$(cat /tmp/body)
    request_id=$(grep -i '^x-request-id:' /tmp/headers | tr -d '\r' | cut -d' ' -f2)
    id_headers=$(grep -ci '^x-request-id:' /tmp/headers)
    if [ "$id_headers" != 1 ]; then
        fail "$1 $2: $id_headers X-Request-ID headers, want exactly 1"
    elif [ "$status" != "$3" ]; then
        fail "$1 $2: HTTP $status, want $3 ($body)"
    elif [ "${body#"$4"}" = "$body" ]; then
        fail "$1 $2: body '$body', want prefix '$4'"
    elif [ -z "$request_id" ]; then
        fail "$1 $2: no X-Request-ID header"
    else
        case "$4" in
            # Proxied: the upstream must have received the same request ID.
            backend*|cache_api*)
                [ "${body##* }" = "$request_id" ] || { fail "$1 $2: upstream got request ID '${body##* }', response has '$request_id'"; return; } ;;
        esac
        pass "$1 $2 -> HTTP $status $(printf '%s' "$body" | cut -c1-60)"
    fi
}

expect_route GET  /healthz                       200 '{"status":"ok","service":"gateway"}'
expect_route POST /v1/routes                     200 'cache_api /v1/routes '
expect_route GET  '/v1/exposure-surface?bbox=106.8,-6.2,106.81,-6.19&zoom=15' 200 'cache_api /v1/exposure-surface?bbox=106.8,-6.2,106.81,-6.19&zoom=15 '
expect_route POST /v1/routes/solve               200 'backend /v1/routes/solve '
expect_route GET  /v1/osm/version                200 'cache_api /v1/osm/version '
expect_route GET  '/v1/osm/roads?mode=walk'      200 'cache_api /v1/osm/roads?mode=walk '
expect_route GET  /v1/pollution/windows          200 'cache_api /v1/pollution/windows '
expect_route GET  /v1/other                      200 'backend /v1/other '
expect_route GET  /health                        404 '{"error":{"code":"not_found"'
expect_route GET  /                              404 '{"error":{"code":"not_found"'

# The upstream receives when the gateway got the request (epoch seconds with ms).
received_at=$(curl -s -X POST -o /dev/null -D - "http://127.0.0.1:$PORT/v1/routes" | grep -i '^x-seen-received-at:' | tr -d '\r' | cut -d' ' -f2)
case "$received_at" in
    [0-9]*.[0-9][0-9][0-9]) pass "X-Request-Received-At forwarded ($received_at)" ;;
    *) fail "X-Request-Received-At not forwarded: '$received_at'" ;;
esac

# expect_json_error METHOD PATH STATUS CODE [curl args...]: NGINX's own errors use the uniform schema.
expect_json_error() {
    method=$1 path=$2 want_status=$3 want_code=$4
    shift 4
    status=$(curl -s -X "$method" -o /tmp/body -D /tmp/headers -w '%{http_code}' "$@" "http://127.0.0.1:$PORT$path")
    body=$(cat /tmp/body)
    request_id=$(grep -i '^x-request-id:' /tmp/headers | tr -d '\r' | cut -d' ' -f2)
    content_type=$(grep -i '^content-type:' /tmp/headers | tr -d '\r' | cut -d' ' -f2)
    case "$body" in
        "{\"error\":{\"code\":\"$want_code\","*"\"request_id\":\"$request_id\"}}") ok=1 ;;
        *) ok=0 ;;
    esac
    if [ "$status" != "$want_status" ] || [ "$ok" != 1 ] || [ "$content_type" != "application/json" ] || [ -z "$request_id" ]; then
        fail "$method $path: HTTP $status $content_type '$body', want $want_status $want_code"
    else
        pass "$method $path -> HTTP $status $want_code (JSON)"
    fi
}

head -c 20000 /dev/zero | tr '\0' 'x' > /tmp/big_body
head -c 16000 /dev/zero | tr '\0' 'x' > /tmp/ok_body
expect_json_error POST /v1/routes 413 payload_too_large --data-binary @/tmp/big_body -H 'Content-Type: application/json'
status=$(curl -s -o /dev/null -w '%{http_code}' --data-binary @/tmp/ok_body "http://127.0.0.1:$PORT/v1/routes")
[ "$status" = 200 ] && pass "POST /v1/routes with 16 000 bytes reaches the Cache API" || fail "16 000-byte body: HTTP $status"
status=$(curl -s -o /dev/null -w '%{http_code}' --data-binary @/tmp/big_body "http://127.0.0.1:$PORT/v1/osm/subgraph")
[ "$status" = 200 ] && pass "16 KiB limit applies to /v1/routes only" || fail "20 000 bytes to /v1/osm/subgraph: HTTP $status"

# Without the stub upstreams, proxied paths get NGINX's 502 in the uniform schema.
rm -f /etc/nginx/conf.d/zz-test-stubs.conf
nginx -s reload
sleep 0.5
expect_json_error POST /v1/routes 502 upstream_unavailable
expect_json_error GET /v1/osm/version 502 upstream_unavailable

nginx -s quit
rm -f /etc/nginx/conf.d/zz-test-stubs.conf /etc/nginx/conf.d/default.conf

if [ "$failures" -gt 0 ]; then
    echo "$failures failure(s)"
    exit 1
fi
echo "all gateway tests passed"
