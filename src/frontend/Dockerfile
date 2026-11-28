FROM node:20-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
# Optional basemap baked into the bundle: a MapLibre style URL (empty = OpenFreeMap "liberty"),
# or raster tiles instead when VITE_BASEMAP_TILES is set.
ARG VITE_BASEMAP_STYLE=""
ARG VITE_BASEMAP_TILES=""
ENV VITE_BASEMAP_STYLE=$VITE_BASEMAP_STYLE \
    VITE_BASEMAP_TILES=$VITE_BASEMAP_TILES
# Optional request timeouts in ms baked into the bundle (empty = 12000 route, 5000 heatmap).
ARG VITE_ROUTE_TIMEOUT_MS=""
ARG VITE_SURFACE_TIMEOUT_MS=""
ENV VITE_ROUTE_TIMEOUT_MS=$VITE_ROUTE_TIMEOUT_MS \
    VITE_SURFACE_TIMEOUT_MS=$VITE_SURFACE_TIMEOUT_MS
# Optional testing tools (empty = off in this production build): the "Uji navigasi" panel
# (true/false) and the GPS spoofer ?lokasi / ?simulasi (on/off). Only for test or demo builds.
ARG VITE_NAV_TEST_PANEL=""
ARG VITE_GPS_SPOOF=""
ENV VITE_NAV_TEST_PANEL=$VITE_NAV_TEST_PANEL \
    VITE_GPS_SPOOF=$VITE_GPS_SPOOF
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
