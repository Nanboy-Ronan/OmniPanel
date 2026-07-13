FROM python:3.13-slim

WORKDIR /app

# libpq is needed at runtime by psycopg2-binary's C extension on some slim images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Overridden by docker-compose.yml: the backend service runs uvicorn,
# the frontend service runs streamlit. This default is just for `docker build && docker run`.
EXPOSE 8000 8501
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
