#!/bin/sh
# Validates the gateway's variables before the nginx image renders the
# template. A bad value would otherwise surface as an obscure "[emerg]" from
# nginx, or worse, as a valid config that proxies to the wrong place.
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

# check_upstream NAME VALUE EXAMPLES: scheme://host:port with nothing after it.
check_upstream() {
    name="$1" upstream="$2" examples="$3"
    [ -n "$upstream" ] || fail "$name is not set; $examples"
    case "$upstream" in
        http://* | https://* ) ;;
        * ) fail "$name='$upstream' must start with http:// or https://" ;;
    esac
    authority="${upstream#*://}"
    case "$authority" in
        '' ) fail "$name='$upstream' has no host" ;;
        */* ) fail "$name='$upstream' must not contain a path or trailing slash (nginx would replace the request path with it); use scheme://host:port" ;;
        # "]" first and "-" last are literal inside a POSIX bracket expression.
        *[!]A-Za-z0-9.:_[-]* ) fail "$name='$upstream' contains characters not allowed in a host:port" ;;
    esac
}

check_upstream BACKEND_UPSTREAM "${BACKEND_UPSTREAM:-}" \
    "Railway: http://backend.railway.internal:8000, compose: http://backend:8000"
check_upstream CACHE_API_UPSTREAM "${CACHE_API_UPSTREAM:-}" \
    "Railway: http://cache-api.railway.internal:8000 (the service built from src/backend/osm/Dockerfile), compose: http://cache_api:8000"

echo "gateway: configuration ok (PORT=$PORT, BACKEND_UPSTREAM=$BACKEND_UPSTREAM, CACHE_API_UPSTREAM=$CACHE_API_UPSTREAM)"
