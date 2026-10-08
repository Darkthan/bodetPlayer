FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home player && mkdir /data && chown player:player /data
COPY app ./app
USER player
CMD ["python", "-m", "app.serve"]
