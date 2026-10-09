FROM python:3.12-slim

WORKDIR /app

# System libs needed by cryptography / Pillow / matplotlib at build time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libjpeg62-turbo-dev \
        zlib1g-dev \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PANEL_PORT=8080

EXPOSE 8080

CMD ["python", "main.py"]
