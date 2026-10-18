# DispatchZ web UI: a static SvelteKit build served by nginx, which also proxies /api to the API container.
#
# Build context is the frontend/ directory:
#   docker build -t dispatchz-web frontend
# For the LinuxONE VM the image is built on the CI runner (or your machine), not on the VM:
#   docker buildx build --platform linux/s390x -t dispatchz-web frontend
# The Node stage always runs on the machine doing the build (--platform=$BUILDPLATFORM), so no s390x Node image and no emulation
# is needed: the build output is plain HTML, CSS and JavaScript. Only the final nginx stage is per-architecture, and it runs
# no commands, only copies files.

# Both bases are pinned by digest (the multi-architecture index), so a republished tag cannot change the build. Update on purpose:
# docs/ci-cd.md, "Updating pinned versions".
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
ARG NGINX_IMAGE=nginx:stable-alpine@sha256:970364228b1e4f932abc22d321932816782cca8a20b85a77552a7cfe9acfb3b5

FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build


FROM ${NGINX_IMAGE} AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build /usr/share/nginx/html
EXPOSE 80
# 127.0.0.1, not localhost: Alpine resolves localhost to ::1 first, and nginx listens on IPv4 only.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
