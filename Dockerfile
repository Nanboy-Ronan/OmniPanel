FROM python:3.13-slim

WORKDIR /app

# libpq supports psycopg2; the client tools run scheduled backups and restores.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Overridden by docker-compose.yml: the backend service runs uvicorn,
# the frontend service runs streamlit. This default is just for `docker build && docker run`.
EXPOSE 8000 8501
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
