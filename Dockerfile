FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    firefox-esr \
    wget \
    ca-certificates \
    fonts-liberation \
    libdbus-glib-1-2 \
    libgtk-3-0 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q https://github.com/mozilla/geckodriver/releases/download/v0.34.0/geckodriver-v0.34.0-linux64.tar.gz \
    && tar -xzf geckodriver-v0.34.0-linux64.tar.gz \
    && mv geckodriver /usr/local/bin/ \
    && rm geckodriver-v0.34.0-linux64.tar.gz

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV FIREFOX_BIN=/usr/bin/firefox-esr
ENV GECKODRIVER_PATH=/usr/local/bin/geckodriver
ENV MOZ_HEADLESS=1
ENV MOZ_DISABLE_CONTENT_SANDBOX=1
ENV MOZ_DISABLE_GMP_SANDBOX=1
ENV MOZ_DISABLE_GPU_SANDBOX=1

CMD ["python", "main.py"]