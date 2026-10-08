FROM mcr.microsoft.com/dotnet/sdk:8.0 AS windows-agent
WORKDIR /agent
COPY windows-agent ./
RUN dotnet publish BodetAgent.csproj -c Release -r win-x64 --self-contained true -o /agent-publish
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home player && mkdir /data && chown player:player /data
COPY app ./app
COPY --from=windows-agent /agent-publish/BodetAgent.exe ./app/downloads/BodetAgent.exe
USER player
CMD ["python", "-m", "app.serve"]
