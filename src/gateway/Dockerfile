FROM nginx:1.27-alpine AS gateway

COPY nginx.conf /etc/nginx/nginx.conf
COPY templates/ /etc/nginx/templates/
COPY docker-entrypoint.d/10-validate-env.sh /docker-entrypoint.d/10-validate-env.sh
RUN chmod +x /docker-entrypoint.d/10-validate-env.sh && rm -f /etc/nginx/conf.d/default.conf

# PORT: Railway injects its own; 8080 is the local default.
# CACHE_API_UPSTREAM has no default on purpose: the start fails with a
# message naming the variable instead of proxying to a guessed host (see
# .env.example).
# GATEWAY_UPSTREAM_TIMEOUT_S: proxy send/read timeout, 10 s per Table 3.15.
# Only these variables are substituted, so nginx's own $variables are safe.
ENV PORT=8080 \
    GATEWAY_UPSTREAM_TIMEOUT_S=10 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    NGINX_ENVSUBST_FILTER="^(PORT|CACHE_API_UPSTREAM|GATEWAY_UPSTREAM_TIMEOUT_S|NGINX_LOCAL_RESOLVERS)$"

EXPOSE 8080


# Environment validation, template render + nginx -t, and routing against stub
# upstreams (tests/run.sh). On its own: docker build --target test src/gateway
FROM gateway AS test
COPY tests/ /tests/
RUN sh /tests/run.sh && touch /tmp/gateway-tests-passed


# Default target. Copying the marker makes every build run the test stage
# first, so a failing test fails the build (and the Railway deploy).
FROM gateway AS runtime
COPY --from=test /tmp/gateway-tests-passed /tmp/gateway-tests-passed
