FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY templates/ /etc/nginx/templates/
COPY docker-entrypoint.d/10-validate-env.sh /docker-entrypoint.d/10-validate-env.sh
RUN chmod +x /docker-entrypoint.d/10-validate-env.sh && rm -f /etc/nginx/conf.d/default.conf

# Fail the build on a syntax error: render the template with sample values
# and run nginx -t, then discard the result (the real values come at start).
RUN PORT=8080 BACKEND_UPSTREAM=http://127.0.0.1:8000 CACHE_API_UPSTREAM=http://127.0.0.1:8001 \
        NGINX_LOCAL_RESOLVERS=127.0.0.11 \
        envsubst '${PORT} ${BACKEND_UPSTREAM} ${CACHE_API_UPSTREAM} ${NGINX_LOCAL_RESOLVERS}' \
        < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf \
    && nginx -t \
    && rm /etc/nginx/conf.d/default.conf

# PORT: Railway injects its own; 8080 is the local default.
# BACKEND_UPSTREAM and CACHE_API_UPSTREAM have no default on purpose: the
# start fails with a message naming the variable instead of proxying to a
# guessed host (see .env.example).
# Only these variables are substituted, so nginx's own $variables are safe.
ENV PORT=8080 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    NGINX_ENVSUBST_FILTER="^(PORT|BACKEND_UPSTREAM|CACHE_API_UPSTREAM|NGINX_LOCAL_RESOLVERS)$"

EXPOSE 8080
