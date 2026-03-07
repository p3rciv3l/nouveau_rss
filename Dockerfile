FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --no-dev --frozen

COPY src/ src/
COPY seed_sites.py .
COPY start.sh .
RUN chmod +x start.sh

# Install Playwright browser
RUN uv run playwright install chromium

EXPOSE 8000

CMD ["./start.sh"]
