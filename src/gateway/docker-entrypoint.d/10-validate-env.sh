#!/bin/sh
# Validates the gateway's variables before the nginx image renders the
# template. A bad value would otherwise surface as an obscure "[emerg]" from
# nginx, or worse, as a valid config that proxies to the wrong place.
# Regression cases: tests/validate-env.cases (run by tests/run.sh).
set -eu

fail() {
    echo "gateway: invalid configuration: $*" >&2
    echo "gateway: set the variable in the service's Variables (Railway) or src/gateway/.env (compose); see src/gateway/README.md" >&2
    exit 1
}

case "${PORT:-}" in
    '' ) fail "PORT is not set (Railway sets it automatically; locally the image default is 8080)" ;;
    *[!0-9]* ) fail "PORT='$PORT' must be a number between 1 and 65535" ;;
esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || fail "PORT='$PORT' must be a number between 1 and 65535"

# The whole value must be scheme://host:port and nothing else: no user-info,
# path, query, fragment, whitespace or nginx syntax. host is a DNS name or IPv4
# address (letters, digits, "-", "_", dot-separated) or a bracketed IPv6
# address. The port is required: Railway services listen on $PORT, not 80.
UPSTREAM_RE='^https?://([A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*|\[[0-9A-Fa-f:.]+\]):[0-9]{1,5}$'
FORMAT_HINT="expected scheme://host:port, e.g. http://cache-api.railway.internal:8000"

# check_upstream NAME VALUE EXAMPLES. A rejected value is never printed: it may
# hold a credential pasted by mistake (e.g. a URL with user:password@).
check_upstream() {
    name="$1" value="$2" examples="$3"
    [ -n "$value" ] || fail "$name is not set; $examples"
    # grep matches line by line, so reject line breaks before using it.
    [ "$(printf '%s' "$value" | wc -l)" -eq 0 ] || fail "$name contains a line break; $FORMAT_HINT (value not shown)"
    printf '%s' "$value" | grep -Eq "$UPSTREAM_RE" || fail "$name is not a valid upstream; $FORMAT_HINT, with no user:password@, path, trailing slash, query or fragment (value not shown)"
    port="${value##*:}"
    [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || fail "$name has port $port; the port must be between 1 and 65535"
}

check_upstream CACHE_API_UPSTREAM "${CACHE_API_UPSTREAM:-}" \
    "Railway: http://cache-api.railway.internal:8000 (the service built from src/backend/osm/Dockerfile), compose: http://cache_api:8000"

# The value passed the check above, so it holds no credentials. BACKEND_UPSTREAM
# is no longer read: no public path goes to the backend (TI-DO-05), and a
# leftover value in the service's variables is ignored.
echo "gateway: configuration ok (PORT=$PORT, CACHE_API_UPSTREAM=$CACHE_API_UPSTREAM)"
