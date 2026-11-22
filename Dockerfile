FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PIP_ROOT_USER_ACTION=ignore PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
# YTDLP_AUTO_UPDATE=1 (default) updates yt-dlp on every start, since platforms change often.
# One worker on purpose: admin settings, click counts and rate limits are kept in memory.
CMD if [ "${YTDLP_AUTO_UPDATE:-1}" = "1" ]; then pip install -q -U "yt-dlp[default]" || true; fi; \
    exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
