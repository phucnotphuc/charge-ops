FROM node:22.20.0-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Public RUM identifiers only (never the Datadog API/APP key). Empty values keep RUM off.
ARG VITE_DD_RUM_APPLICATION_ID=
ARG VITE_DD_RUM_CLIENT_TOKEN=
ARG VITE_DD_SITE=datadoghq.com
ARG VITE_DD_ENV=hackathon
ARG VITE_DD_VERSION=0.1.0
ENV VITE_DD_RUM_APPLICATION_ID=$VITE_DD_RUM_APPLICATION_ID \
    VITE_DD_RUM_CLIENT_TOKEN=$VITE_DD_RUM_CLIENT_TOKEN \
    VITE_DD_SITE=$VITE_DD_SITE \
    VITE_DD_ENV=$VITE_DD_ENV \
    VITE_DD_VERSION=$VITE_DD_VERSION
RUN npm run build

FROM caddy:2.9.1-alpine
COPY --from=build /web/dist /srv
