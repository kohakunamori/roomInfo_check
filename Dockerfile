# syntax=docker/dockerfile:1

# ── Shared base ───────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates nodejs tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Runtime: web dashboard + monitor (Plan A, no browser) ─────────
FROM base AS runtime

COPY requirements-server.txt /app/requirements-server.txt
RUN pip install --upgrade pip \
    && pip install -r /app/requirements-server.txt

COPY main.py index.py webapp.py monitor.py history.py auth_control.py \
     settings_store.py getInfo.py env.py emailSend.py encrypt.js data.json \
     /app/
COPY templates /app/templates
COPY static /app/static
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/data /app/config

VOLUME ["/app/data", "/app/config"]
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "main.py"]

# ── Auth: headful Chromium + Xvfb + noVNC (on-demand profile) ─────
FROM base AS auth

# Browser + virtual display + VNC stack
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        chromium-sandbox \
        fonts-noto-cjk \
        fonts-liberation \
        libnss3 \
        libatk-bridge2.0-0 \
        libgtk-3-0 \
        libx11-xcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libgbm1 \
        libasound2 \
        libpangocairo-1.0-0 \
        libpango-1.0-0 \
        libcups2 \
        libdrm2 \
        libxshmfence1 \
        xvfb \
        x11vnc \
        openbox \
        novnc \
        websockify \
        procps \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt \
    && playwright install-deps chromium \
    && playwright install chromium

COPY main.py index.py webapp.py monitor.py history.py auth_control.py auth_worker.py \
     settings_store.py browser_session.py bootstrap_browser.py refresh_credentials.py \
     getInfo.py env.py emailSend.py encrypt.js data.json \
     /app/
COPY templates /app/templates
COPY static /app/static
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/auth-entrypoint.sh /auth-entrypoint.sh
RUN chmod +x /entrypoint.sh /auth-entrypoint.sh \
    && mkdir -p /app/data /app/config \
    && ln -sf /usr/share/novnc /opt/novnc || true

ENV DISPLAY=:99 \
    ONLINE_BROWSER_EXECUTABLE=/usr/bin/chromium \
    ONLINE_BROWSER_REFRESH=true \
    ONLINE_SESSION_FILE=./data/.uestc_session.json \
    ONLINE_BROWSER_STATE_FILE=./data/.uestc_browser_state.json \
    ONLINE_BROWSER_PROFILE_DIR=./data/.uestc_chrome_profile \
    NOVNC_HOME=/usr/share/novnc \
    AUTH_TIMEOUT_SECONDS=900

VOLUME ["/app/data", "/app/config"]
EXPOSE 6080
ENTRYPOINT ["/auth-entrypoint.sh"]
