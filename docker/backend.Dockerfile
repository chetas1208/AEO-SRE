FROM python:3.12-slim
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install --no-cache-dir -e .
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
