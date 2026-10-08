# syntax=docker/dockerfile:1

# Stage 1: system libraries for Chromium + npm dependencies.
# Changes only when package-lock.json / OS deps change — not when app code changes.
FROM node:22-bookworm AS deps
WORKDIR /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY package.json package-lock.json ./
RUN npm ci

# Install OS packages required by Chromium, then the browser itself.
RUN npx playwright install-deps chromium \
  && npx playwright install chromium

# Stage 2: runtime image.
# Inherits browsers + node_modules from deps; only app sources are added here.
FROM deps AS runtime
WORKDIR /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV NODE_ENV=production

COPY src ./src

CMD ["node", "src/scrape.js"]
