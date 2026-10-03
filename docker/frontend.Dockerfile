FROM node:22-slim
RUN corepack enable
WORKDIR /app
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend ./
RUN pnpm build
ENV HOST=0.0.0.0 PORT=3000
CMD ["node", ".output/server/index.mjs"]
