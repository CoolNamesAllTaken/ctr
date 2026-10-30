FROM node:24-slim AS base
WORKDIR /app

# Install dependencies
COPY package.json package-lock.json ./
RUN npm ci


# Development (compose.dev.yml): the Next dev server
FROM base AS dev

COPY . .

CMD ["npm", "run", "dev"]


FROM base AS build

# The /api rewrites in next.config.ts are fixed at build time, so the Django
# URL has to be known now as well as at runtime.
ARG API_URL
ENV API_URL=$API_URL

COPY . .
RUN npm run build


# Production, the default target: the standalone server on $PORT
FROM node:24-slim AS prod
WORKDIR /app

ENV NODE_ENV=production \
    # Docker sets HOSTNAME to the container ID; listen on every interface
    HOSTNAME=0.0.0.0

COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

CMD ["node", "server.js"]
