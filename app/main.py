"""Audio PCM relay with native Harmonys streaming and external bridge support."""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import sys
import time
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt
from app.playback import Library, AudioPlayer, MAX_FILE_BYTES, MAX_LIBRARY_BYTES, spawn, terminate
from app.audio import audio_settings
from app.agents import AgentRegistry

STATIC = Path(__file__).parent / "static"
CONFIG = json.loads(Path(os.getenv("PLAYER_CONFIG", "config/player.json")).read_text(encoding="utf-8"))
SETTINGS_PATH = Path(os.getenv("PLAYER_SETTINGS", "data/settings.json"))


def multicast_address(value):
    try:
        address = ipaddress.IPv4Address(value.strip())
    except (ValueError, AttributeError):
        raise ValueError("Saisissez une adresse multicast IPv4 valide (224.0.0.0 à 239.255.255.255).")
    if not address.is_multicast:
        raise ValueError("Cette adresse IPv4 n’est pas une adresse multicast (224.0.0.0 à 239.255.255.255).")
    return str(address)


def load_multicast():
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8")) if SETTINGS_PATH.exists() else CONFIG
    return multicast_address(settings.get("multicast_address", "239.192.55.1"))


MULTICAST = load_multicast()
ZONES = (json.loads(SETTINGS_PATH.read_text(encoding="utf-8")).get("zones", CONFIG["zones"])
         if SETTINGS_PATH.exists() else CONFIG["zones"])


def save_settings(address, zones, audio=None):
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps({"multicast_address": address, "zones": zones,
                                        "audio": audio if audio is not None else AUDIO},
                                        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(SETTINGS_PATH)
    except OSError:
        raise HTTPException(500, "Impossible d’enregistrer les paramètres.")


PASSWORD = os.getenv("PLAYER_PASSWORD", "")


def allowed_origins(value):
    origins = set()
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            url = urlsplit(entry)
            if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
                    or url.path not in ("", "/") or url.query or url.fragment or "*" in entry):
                raise ValueError
            host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
            port = url.port
            suffix = f":{port}" if port is not None and port != (443 if url.scheme == "https" else 80) else ""
            origins.add(f"{url.scheme}://{host}{suffix}")
        except ValueError:
            raise ValueError("PLAYER_ORIGIN doit contenir des origines HTTP/HTTPS séparées par des virgules, sans chemin.")
    if not origins:
        raise ValueError("PLAYER_ORIGIN ne contient aucune origine autorisée.")
    return frozenset(origins)


ORIGINS = allowed_origins(os.getenv("PLAYER_ORIGIN") or
                          f"http://{os.getenv('PLAYER_HOST', '127.0.0.1')}:{os.getenv('PLAYER_PORT', '8080')}")
KEY = secrets.token_bytes(32)


@asynccontextmanager
async def lifespan(application):
    yield
    if music_player:
        await music_player.stop()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
lock = asyncio.Lock()
active = None
music_player = None
attempts = {}
def load_audio():
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8")) if SETTINGS_PATH.exists() else {}
    return audio_settings(settings.get("audio"))


AUDIO = load_audio()
AGENTS = AgentRegistry(lambda: SETTINGS_PATH)


def valid(token):
    try:
        expiry, signature = token.split(".")
        expected = hmac.new(KEY, expiry.encode(), hashlib.sha256).hexdigest()
        return int(expiry) > time.time() and hmac.compare_digest(signature, expected)
    except (ValueError, AttributeError):
        return False


def authorize(request):
    if not any(valid(request.cookies.get(name)) for name in ("session", "session_https")):
        raise HTTPException(401, "Connectez-vous.")


def same_origin(request):
    if request.headers.get("origin") not in ORIGINS:
        raise HTTPException(403, "Origine refusée.")


def session_cookie(origin):
    # Separate names prevent HTTP logins overwriting a Secure cookie on the
    # same hostname, and preserve Secure protection for HTTPS sessions.
    return "session_https" if origin.startswith("https://") else "session"


def websocket_session(ws):
    origin = ws.headers.get("origin", "")
    return origin in ORIGINS and valid(ws.cookies.get(session_cookie(origin)))


class Login(BaseModel):
    password: str = Field(max_length=256)


class NetworkSettings(BaseModel):
    multicast_address: str = Field(max_length=64)


class AudioSettings(BaseModel):
    block_ms: StrictInt = Field(ge=10, le=100)
    max_backlog_ms: StrictInt = Field(ge=40, le=1000)


class AgentPair(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)


class AgentStart(BaseModel):
    zones: list[StrictInt] = Field(min_length=1, max_length=100)


class ZoneSettings(BaseModel):
    id: int = Field(ge=1, le=100, strict=True)
    name: str = Field(min_length=1, max_length=80)


class PlayerControl(BaseModel):
    action: Literal["previous", "next", "loop"]
    loop: Literal["off", "track", "playlist"] = "off"


class LibraryStart(BaseModel):
    tracks: list[str] = Field(min_length=1, max_length=100)
    zones: list[StrictInt] = Field(min_length=1, max_length=100)
    loop: Literal["off", "track", "playlist"] = "off"


class QueueOrder(BaseModel):
    order: list[str] = Field(min_length=1, max_length=100)


class PlaylistSettings(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    tracks: list[str] = Field(max_length=100)


class LibraryDelete(BaseModel):
    tracks: list[str] = Field(min_length=1, max_length=1000)


def saved_playlists():
    path = SETTINGS_PATH.parent / "playlists.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError):
        raise HTTPException(500, "Impossible de lire les playlists enregistrées.")


def save_playlists(playlists):
    path = SETTINGS_PATH.parent / "playlists.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(playlists, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        raise HTTPException(500, "Impossible d’enregistrer les playlists.")


def audio_library():
    # Preserve the existing library location across updates.
    return Library(SETTINGS_PATH.parent / "youtube" / "library")


@app.get("/")
async def home():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/api/login")
async def login(body: Login, request: Request):
    same_origin(request)
    # Global bound prevents spoofed forwarded headers from bypassing throttling.
    now = time.monotonic()
    recent = [t for t in attempts.get("login", []) if now - t < 60]
    attempts["login"] = recent
    if len(recent) >= 10:
        raise HTTPException(429, "Réessayez dans une minute.")
    recent.append(now)
    if not PASSWORD or not secrets.compare_digest(body.password, PASSWORD):
        raise HTTPException(401, "Mot de passe incorrect.")
    expiry = str(int(time.time()) + 28800)
    token = expiry + "." + hmac.new(KEY, expiry.encode(), hashlib.sha256).hexdigest()
    response = JSONResponse({"ok": True})
    origin = request.headers["origin"]
    response.set_cookie(session_cookie(origin), token, httponly=True, secure=origin.startswith("https://"), samesite="strict", max_age=28800)
    return response


@app.post("/api/logout")
async def logout(request: Request):
    same_origin(request)
    authorize(request)
    response = JSONResponse({"ok": True})
    response.delete_cookie("session")
    if request.headers["origin"].startswith("https://"):
        response.delete_cookie("session_https", secure=True, httponly=True, samesite="strict")
    return response


@app.get("/api/status")
async def status(request: Request):
    authorize(request)
    return {"mode": CONFIG["mode"], "zones": ZONES, "active": active,
            "multicast_address": MULTICAST,
            "player": music_player.snapshot() if music_player else None,
            "library": audio_library().list(),
            "playlists": saved_playlists(),
            "agents": AGENTS.snapshot(),
            "audio": {"block_ms": AUDIO["block_ms"], "max_backlog_ms": AUDIO["max_backlog_ms"]}}


async def playback_finished(session):
    global active
    async with lock:
        if active is session:
            active = None


@app.post("/api/player/control")
async def player_control(body: PlayerControl, request: Request):
    same_origin(request)
    authorize(request)
    async with lock:
        if not active or active.get("kind") != "playlist" or not music_player:
            raise HTTPException(409, "Aucune playlist en cours.")
        if body.action == "loop":
            music_player.state["loop"] = body.loop
        else:
            try:
                music_player.navigate(1 if body.action == "next" else -1)
            except ValueError as exc:
                raise HTTPException(409, str(exc))
        return music_player.snapshot()


@app.post("/api/library/start")
async def library_start(body: LibraryStart, request: Request):
    global active, music_player
    same_origin(request)
    authorize(request)
    if not shutil.which("ffmpeg"):
        raise HTTPException(503, "FFmpeg est nécessaire. Reconstruisez l’image Docker.")
    async with lock:
        if active:
            raise HTTPException(409, "Une diffusion est déjà en cours.")
        if any(zone not in {z["id"] for z in ZONES} for zone in body.zones):
            raise HTTPException(422, "Sélectionnez des zones valides.")
        try:
            tracks = [audio_library().get(video_id)[0] for video_id in body.tracks]
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        session = {"zones": sorted(set(body.zones)), "bytes": 0, "stop": False,
                   "source": "Bibliothèque", "kind": "playlist", "started": time.time(),
                   "multicast_address": MULTICAST, "audio": dict(AUDIO)}
        player = AudioPlayer(session, CONFIG, SETTINGS_PATH.parent / "youtube", playback_finished)
        active = session
        music_player = player
        player.start_tracks(tracks, body.loop)
    return player.snapshot()


@app.post("/api/player/order")
async def player_order(body: QueueOrder, request: Request):
    same_origin(request)
    authorize(request)
    async with lock:
        if not active or active.get("kind") != "playlist" or not music_player or not music_player.state["queue"]:
            raise HTTPException(409, "Aucune file en cours.")
        try:
            music_player.reorder(body.order)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return music_player.snapshot()


@app.post("/api/library/upload")
async def upload_library_track(request: Request):
    same_origin(request)
    authorize(request)
    if not shutil.which("ffmpeg"):
        raise HTTPException(503, "Reconstruisez l’image Docker pour installer FFmpeg.")
    title = request.query_params.get("name", "Son importé").strip()[:200] or "Son importé"
    async with lock:
        if active:
            raise HTTPException(409, "Arrêtez la diffusion avant d’importer des sons.")
        library = audio_library()
        used = sum(track["size"] for track in library.list())
        library.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=library.directory) as temporary:
            source = Path(temporary) / "input"
            size = 0
            with source.open("wb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise HTTPException(413, "Chaque son est limité à 100 Mio.")
                    output.write(chunk)
            if not size:
                raise HTTPException(422, "Le fichier est vide.")
            target = Path(temporary) / "audio.mp3"
            process = await spawn("ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
                                  "-i", str(source), "-map", "0:a:0", "-vn", "-t", "3600",
                                  "-codec:a", "libmp3lame", "-b:a", "128k", "-fs", str(MAX_FILE_BYTES + 1),
                                  str(target), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                await asyncio.wait_for(process.wait(), 300)
                if process.returncode or not target.exists() or not target.stat().st_size:
                    raise HTTPException(422, "Ce fichier ne contient pas de son lisible.")
                converted_size = target.stat().st_size
                if converted_size > MAX_FILE_BYTES or used + converted_size > MAX_LIBRARY_BYTES:
                    raise HTTPException(413, "Bibliothèque pleine (1 Gio) ou son trop volumineux.")
                track_id = secrets.token_urlsafe(9)[:11]
                while (library.directory / (track_id + ".json")).exists():
                    track_id = secrets.token_urlsafe(9)[:11]
                track = {"id": track_id, "title": title}
                library.save(track, target)
                return track
            except asyncio.TimeoutError:
                raise HTTPException(408, "L’import audio a dépassé cinq minutes.")
            finally:
                await terminate(process)


async def delete_library_tracks(tracks, request):
    same_origin(request)
    authorize(request)
    async with lock:
        if active:
            raise HTTPException(409, "Arrêtez la diffusion avant de supprimer une piste.")
        try:
            library = audio_library()
            unique = set(tracks)
            # Validate the whole selection before deleting any file.
            for track_id in unique:
                library.get(track_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        playlists = saved_playlists()
        for playlist in playlists:
            playlist["tracks"] = [track_id for track_id in playlist["tracks"] if track_id not in unique]
        save_playlists(playlists)
        for track_id in unique:
            library.delete(track_id)
    return {"deleted": len(unique)}


@app.post("/api/library/delete")
async def bulk_delete_library(body: LibraryDelete, request: Request):
    return await delete_library_tracks(body.tracks, request)


@app.delete("/api/library/{video_id}")
async def delete_library_track(video_id: str, request: Request):
    await delete_library_tracks([video_id], request)
    return {"ok": True}


async def write_playlist(body, request, playlist_id=None):
    same_origin(request)
    authorize(request)
    async with lock:
        if active:
            raise HTTPException(409, "Arrêtez la diffusion avant de modifier les playlists.")
        playlists = saved_playlists()
        existing = next((p for p in playlists if p["id"] == playlist_id), None)
        if playlist_id is not None and existing is None:
            raise HTTPException(404, "Playlist introuvable.")
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "Donnez un nom à la playlist.")
        try:
            for track_id in body.tracks:
                audio_library().get(track_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        if existing:
            existing.update(name=name, tracks=body.tracks)
            result = existing
        else:
            if len(playlists) >= 100:
                raise HTTPException(409, "La limite de 100 playlists est atteinte.")
            result = {"id": secrets.token_hex(16), "name": name, "tracks": body.tracks}
            playlists.append(result)
        save_playlists(playlists)
        return result


@app.post("/api/playlists")
async def create_playlist(body: PlaylistSettings, request: Request):
    return await write_playlist(body, request)


@app.put("/api/playlists/{playlist_id}")
async def update_playlist(playlist_id: str, body: PlaylistSettings, request: Request):
    return await write_playlist(body, request, playlist_id)


@app.delete("/api/playlists/{playlist_id}")
async def delete_playlist(playlist_id: str, request: Request):
    same_origin(request)
    authorize(request)
    async with lock:
        if active:
            raise HTTPException(409, "Arrêtez la diffusion avant de supprimer une playlist.")
        playlists = saved_playlists()
        remaining = [p for p in playlists if p["id"] != playlist_id]
        if len(remaining) == len(playlists):
            raise HTTPException(404, "Playlist introuvable.")
        save_playlists(remaining)
    return {"ok": True}


@app.post("/api/settings/audio")
async def update_audio_settings(body: AudioSettings, request: Request):
    global AUDIO
    same_origin(request)
    authorize(request)
    try:
        settings = audio_settings(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    async with lock:
        if active:
            raise HTTPException(409, "Arrêtez la diffusion avant de modifier le buffer.")
        save_settings(MULTICAST, ZONES, settings)
        AUDIO = settings
    return {"block_ms": AUDIO["block_ms"], "max_backlog_ms": AUDIO["max_backlog_ms"]}


@app.post("/api/settings/network")
async def network_settings(body: NetworkSettings, request: Request):
    global MULTICAST
    same_origin(request)
    authorize(request)
    try:
        address = multicast_address(body.multicast_address)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    async with lock:
        if active:
            raise HTTPException(409, "Arrêtez la diffusion avant de modifier l’adresse multicast.")
        save_settings(address, ZONES)
        MULTICAST = address
    return {"multicast_address": MULTICAST}


async def change_zone(request, body=None, original_id=None):
    global ZONES
    same_origin(request)
    authorize(request)
    async with lock:
        if active:
            raise HTTPException(409, "Arrêtez la diffusion avant de modifier les zones.")
        if original_id is not None and not any(z["id"] == original_id for z in ZONES):
            raise HTTPException(404, "Zone introuvable.")
        zones = [dict(z) for z in ZONES if z["id"] != original_id]
        if body is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(422, "Le nom de la zone ne peut pas être vide.")
            if any(z["id"] == body.id for z in zones):
                raise HTTPException(409, "Ce numéro de zone existe déjà.")
            zones.append({"id": body.id, "name": name})
        zones.sort(key=lambda z: z["id"])
        save_settings(MULTICAST, zones)
        ZONES = zones
    return {"zones": ZONES}


@app.post("/api/settings/zones")
async def create_zone(body: ZoneSettings, request: Request):
    return await change_zone(request, body)


@app.put("/api/settings/zones/{zone_id}")
async def update_zone(zone_id: int, body: ZoneSettings, request: Request):
    return await change_zone(request, body, zone_id)


@app.delete("/api/settings/zones/{zone_id}")
async def delete_zone(zone_id: int, request: Request):
    return await change_zone(request, original_id=zone_id)


@app.get("/api/agents/download")
async def download_agent(request: Request):
    authorize(request)
    path = Path(os.getenv("PLAYER_AGENT_DOWNLOAD", str(Path(__file__).parent / "downloads" / "BodetAgent.exe")))
    if not path.is_file():
        raise HTTPException(503, "Reconstruisez l’image Docker pour inclure l’agent Windows.")
    return FileResponse(path, filename="BodetAgent.exe", media_type="application/octet-stream")


@app.post("/api/agents/pairing")
async def pairing_code(request: Request):
    same_origin(request)
    authorize(request)
    async with lock:
        return AGENTS.code()


@app.post("/api/agents/pair")
async def pair_agent(body: AgentPair):
    # Native clients have no browser cookie. The random one-time code is their credential.
    async with lock:
        return AGENTS.pair(body.code, body.name)


@app.delete("/api/agents/{agent_id}")
async def revoke_agent(agent_id: str, request: Request):
    same_origin(request)
    authorize(request)
    async with lock:
        records = AGENTS.records()
        remaining = [record for record in records if record["id"] != agent_id]
        if len(remaining) == len(records):
            raise HTTPException(404, "Agent introuvable.")
        AGENTS.save(remaining)
        if active and active.get("agent_id") == agent_id:
            active["stop"] = True
        connection = AGENTS.connections.get(agent_id)
    if connection:
        await connection["socket"].close(code=1008)
    return {"ok": True}


@app.post("/api/agents/{agent_id}/start")
async def start_agent(agent_id: str, body: AgentStart, request: Request):
    global active
    same_origin(request)
    authorize(request)
    async with lock:
        if active:
            raise HTTPException(409, "Une diffusion est déjà en cours.")
        connection = AGENTS.connections.get(agent_id)
        if not connection:
            raise HTTPException(409, "Cet agent est hors ligne. Lancez-le sur le PC Windows.")
        if any(zone not in {z["id"] for z in ZONES} for zone in body.zones):
            raise HTTPException(422, "Sélectionnez des zones valides.")
        record = next((record for record in AGENTS.records() if record["id"] == agent_id), None)
        if not record:
            raise HTTPException(404, "Agent introuvable.")
        session = {"zones": sorted(set(body.zones)), "bytes": 0, "stop": False,
                   "source": record["name"], "kind": "agent", "agent_id": agent_id,
                   "agent_session": secrets.token_urlsafe(24), "started": time.time(),
                   "multicast_address": MULTICAST, "audio": dict(AUDIO), "agent_connected": False}
        active = session
        connection["error"] = ""
    try:
        await asyncio.wait_for(connection["socket"].send_json({"command": "start",
            "session": session["agent_session"], "zones": session["zones"],
            "audio": {"block_ms": AUDIO["block_ms"], "max_backlog_ms": AUDIO["max_backlog_ms"]}}), 3)
    except (RuntimeError, OSError, asyncio.TimeoutError):
        async with lock:
            if active is session:
                active = None
        raise HTTPException(409, "La connexion avec l’agent a été interrompue.")
    return {"ok": True}


@app.websocket("/api/agents/control")
async def agent_control(ws: WebSocket):
    global active
    record = AGENTS.authenticate(ws.headers.get("authorization"))
    if not record:
        await ws.close(code=1008)
        return
    await ws.accept()
    agent_id = record["id"]
    connection = {"socket": ws, "status": "idle", "error": "", "device": ""}
    async with lock:
        if agent_id in AGENTS.connections:
            await ws.close(code=1008)
            return
        AGENTS.connections[agent_id] = connection
    try:
        await ws.send_json({"command": "connected"})
        last = time.monotonic()
        while AGENTS.authenticate(ws.headers.get("authorization")):
            try:
                report = await asyncio.wait_for(ws.receive_json(), 1)
                if not isinstance(report, dict):
                    raise ValueError("Rapport invalide.")
                last = time.monotonic()
                connection["status"] = "capturing" if report.get("status") == "capturing" else "idle"
                connection["error"] = str(report.get("error", ""))[:300]
                connection["device"] = str(report.get("device", ""))[:200]
                if (report.get("status") in ("stopped", "error") and active and active.get("agent_id") == agent_id
                        and report.get("session") == active["agent_session"]):
                    active["stop"] = True
            except asyncio.TimeoutError:
                if time.monotonic() - last > 15:
                    break
            async with lock:
                if active and active.get("agent_id") == agent_id and not active["agent_connected"]:
                    if active["stop"] or time.time() - active["started"] > 10:
                        connection["error"] = connection["error"] or "L’agent n’a pas démarré la capture."
                        active = None
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        async with lock:
            if AGENTS.connections.get(agent_id) is connection:
                AGENTS.connections.pop(agent_id)
            if active and active.get("agent_id") == agent_id:
                active["stop"] = True
                if not active["agent_connected"]:
                    active = None


@app.post("/api/stop")
async def stop(request: Request):
    same_origin(request)
    authorize(request)
    if active:
        active["stop"] = True
        if active.get("kind") == "playlist" and music_player:
            await music_player.stop()
        elif active.get("kind") == "agent":
            connection = AGENTS.connections.get(active["agent_id"])
            if connection:
                try:
                    await asyncio.wait_for(connection["socket"].send_json({"command": "stop", "session": active["agent_session"]}), 3)
                except (RuntimeError, OSError, asyncio.TimeoutError):
                    pass
    return {"ok": True}


@app.websocket("/api/live")
async def live(ws: WebSocket):
    global active
    agent = AGENTS.authenticate(ws.headers.get("authorization"))
    if not agent and not websocket_session(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    session = None
    process = None
    try:
        settings = await asyncio.wait_for(ws.receive_json(), 10)
        selected = settings.get("zones", [])
        async with lock:
            if agent:
                if (not active or active.get("agent_id") != agent["id"] or active["stop"]
                        or active.get("agent_connected") or settings.get("agent_session") != active["agent_session"]):
                    raise ValueError("Aucune capture autorisée pour cet agent.")
                session = active
                session["agent_connected"] = True
                selected = session["zones"]
            allowed = {z["id"] for z in ZONES}
            if not selected or any(type(z) is not int or z not in allowed for z in selected):
                raise ValueError("Sélectionnez des zones valides.")
            if active and not agent:
                raise ValueError("Une diffusion est déjà en cours.")
            if not agent:
                session = {"zones": sorted(set(selected)), "bytes": 0, "stop": False,
                           "source": str(settings.get("source", "PC"))[:80], "started": time.time(),
                           "multicast_address": MULTICAST}
                active = session
        if CONFIG["mode"] in ("bridge", "bodet"):
            command = CONFIG.get("bridge_command", [])
            if CONFIG["mode"] == "bodet":
                if not shutil.which("ffmpeg"):
                    raise ValueError("FFmpeg est nécessaire à la diffusion Bodet. Reconstruisez l’image Docker.")
                command = [sys.executable, "-m", "app.bodet_bridge"]
            if not command or not all(isinstance(x, str) for x in command):
                raise ValueError("Passerelle audio non configurée.")
            env = dict(os.environ, BODET_ZONES=json.dumps(session["zones"]),
                       BODET_MULTICAST_ADDRESS=session["multicast_address"],
                       AUDIO_FORMAT="s16le", AUDIO_RATE="48000", AUDIO_CHANNELS="1")
            env["BODET_QUALITY"] = CONFIG.get("bodet_quality", "low")
            env["BODET_INTERFACE"] = CONFIG.get("bodet_interface", "")
            env["PLAYER_AUDIO_BLOCK_MS"] = str(AUDIO["block_ms"])
            env["PLAYER_AUDIO_MAX_BACKLOG_MS"] = str(AUDIO["max_backlog_ms"])
            process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                                                          stdout=asyncio.subprocess.DEVNULL, env=env)
            process.stdin.transport.set_write_buffer_limits(high=AUDIO["max_backlog_bytes"],
                                                           low=AUDIO["max_backlog_bytes"] // 2)
        elif CONFIG["mode"] != "simulation":
            raise ValueError("Mode audio inconnu.")
        await ws.send_json({"ready": True, "mode": CONFIG["mode"]})
        start = time.monotonic()
        while not session["stop"]:
            if (agent and not AGENTS.authenticate(ws.headers.get("authorization"))) or (not agent and not websocket_session(ws)):
                break
            try:
                data = await asyncio.wait_for(ws.receive_bytes(), 1)
            except asyncio.TimeoutError:
                if time.time() - session.get("last", session["started"]) > 10:
                    raise ValueError("Source audio interrompue.")
                continue
            if not data or len(data) > 32768 or len(data) % 2:
                raise ValueError("Bloc audio invalide.")
            session["bytes"] += len(data)
            session["last"] = time.time()
            if session["bytes"] > (time.monotonic() - start + 2) * 96000:
                raise ValueError("Débit audio excessif.")
            if process:
                if process.returncode is not None:
                    raise ValueError("La passerelle audio s’est arrêtée.")
                process.stdin.write(data)
                await asyncio.wait_for(process.stdin.drain(), 2)
    except WebSocketDisconnect:
        pass
    except (ValueError, asyncio.TimeoutError, OSError, BrokenPipeError) as exc:
        try:
            await ws.send_json({"error": str(exc) or "Délai dépassé."})
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        async with lock:
            if session is not None and active is session:
                active = None
        if agent and session:
            connection = AGENTS.connections.get(agent["id"])
            if connection:
                try:
                    await asyncio.wait_for(connection["socket"].send_json({"command": "stop", "session": session["agent_session"]}), 3)
                except (RuntimeError, OSError, asyncio.TimeoutError):
                    pass
        try:
            await ws.close()
        except RuntimeError:
            pass
