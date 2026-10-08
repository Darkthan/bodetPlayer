FROM node:22-bookworm-slim AS youtube-runtime
FROM python:3.12-slim-bookworm
COPY --from=youtube-runtime /usr/local/bin/node /usr/local/bin/node
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates libstdc++6 && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home player && mkdir /data && chown player:player /data
COPY app ./app
USER player
CMD ["python", "-m", "app.serve"]
