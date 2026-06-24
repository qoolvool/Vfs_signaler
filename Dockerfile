FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        # Patchright/Chromium runtime dependencies
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
        libpango-1.0-0 libcairo2 libasound2 libxshmfence1 libx11-xcb1 \
        libxfixes3 fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m patchright install chromium

COPY vfs_bot/ vfs_bot/
COPY config.example.yaml .

RUN mkdir -p /app/debug

VOLUME ["/app/debug"]

CMD ["python", "-m", "vfs_bot", "--no-gui"]
