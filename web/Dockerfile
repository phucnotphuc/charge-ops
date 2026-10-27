FROM node:22.20.0-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2.9.1-alpine
COPY --from=build /web/dist /srv
