FROM node:24-bookworm-slim AS web-build
WORKDIR /app/web
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY raft ./raft
RUN pip install --no-cache-dir .
COPY model-roles.yaml ./model-roles.yaml
COPY --from=web-build /app/web/dist ./web/dist
RUN mkdir -p /data
EXPOSE 8000
CMD ["uvicorn", "raft.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

