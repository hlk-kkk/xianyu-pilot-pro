FROM node:24.18.0-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN node --version \
    && npm --version \
    && npm ci --no-audit --no-fund
COPY . .
ARG VITE_TERMS_URL=
ARG VITE_PRIVACY_URL=
ENV VITE_TERMS_URL=${VITE_TERMS_URL} \
    VITE_PRIVACY_URL=${VITE_PRIVACY_URL}
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.30.3-alpine
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
