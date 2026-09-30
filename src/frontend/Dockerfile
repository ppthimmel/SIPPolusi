FROM node:20-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# nginx:alpine renders /etc/nginx/templates/*.template with envsubst at start
COPY default.conf.template /etc/nginx/templates/default.conf.template
EXPOSE 80
