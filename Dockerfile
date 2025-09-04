FROM python:3.11-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # allow PROJ to fetch grids if needed
    PROJ_NETWORK=ON

# minimal OS deps (no GDAL dev packages needed for binary wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .

# Force binary wheels (no source builds)
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

COPY . .
ENV PORT=8000
EXPOSE 8000
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000"]
