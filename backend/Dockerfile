FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
EXPOSE 8000

