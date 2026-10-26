FROM node:20-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
# Optional basemap tile URL baked into the bundle (empty = OpenStreetMap default).
ARG VITE_BASEMAP_TILES=""
ENV VITE_BASEMAP_TILES=$VITE_BASEMAP_TILES
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# nginx:alpine renders /etc/nginx/templates/*.template with envsubst at start
COPY default.conf.template /etc/nginx/templates/default.conf.template
# Defaults for local compose; on Railway set
# GATEWAY_UPSTREAM=http://${{gateway.RAILWAY_PRIVATE_DOMAIN}}:${{gateway.PORT}}.
# Railway injects PORT for the public domain.
ENV PORT=80 \
    GATEWAY_UPSTREAM=http://gateway:8080 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
EXPOSE 80
