# The web app's image (docs/decisions/ui-build.md ruling U13): a production build served by `next start`. It
# holds no key and no .env: compose gives this service only SMALLS_API_URL, the API's address on the compose
# network, which the server reads and the browser never sees.
FROM node:24-bookworm-slim AS deps
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM deps AS build
COPY . .
# The event stream's rewrite is fixed at build time, so the build is told the API's address on the network.
ENV SMALLS_API_URL=http://api:7318
RUN pnpm build

FROM node:24-bookworm-slim
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 SMALLS_WEB_PORT=7317
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --prod && rm -rf /root/.cache /root/.local/share/pnpm
COPY --from=build /web/.next ./.next
COPY next.config.ts ./
USER node
EXPOSE 7317
CMD ["sh", "-c", "exec node_modules/.bin/next start --port ${SMALLS_WEB_PORT}"]
