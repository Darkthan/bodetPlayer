"""Audio PCM relay with native Harmonys streaming and external bridge support."""
import asyncio
import importlib.util
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt
from app.youtube import Library, YouTubePlayer, youtube_url

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


def save_settings(address, zones):
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps({"multicast_address": address, "zones": zones},
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
    if youtube_player:
        await youtube_player.stop()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
lock = asyncio.Lock()
active = None
youtube_player = None
attempts = {}


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


class ZoneSettings(BaseModel):
    id: int = Field(ge=1, le=100, strict=True)
    name: str = Field(min_length=1, max_length=80)


class YouTubeStart(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    zones: list[StrictInt] = Field(min_length=1, max_length=100)
    loop: Literal["off", "track", "playlist"] = "off"


class YouTubeControl(BaseModel):
    action: Literal["previous", "next", "loop"]
    loop: Literal["off", "track", "playlist"] = "off"


class LibraryStart(BaseModel):
    tracks: list[str] = Field(min_length=1, max_length=100)
    zones: list[StrictInt] = Field(min_length=1, max_length=100)
    loop: Literal["off", "track", "playlist"] = "off"


class QueueOrder(BaseModel):
    order: list[str] = Field(min_length=1, max_length=100)


def audio_library():
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
            "youtube": youtube_player.snapshot() if youtube_player else None,
            "library": audio_library().list()}


async def youtube_finished(session):
    global active
    async with lock:
        if active is session:
            active = None


@app.post("/api/youtube/start")
async def youtube_start(body: YouTubeStart, request: Request):
    global active, youtube_player
    same_origin(request)
    authorize(request)
    try:
        url = youtube_url(body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not importlib.util.find_spec("yt_dlp") or not shutil.which("ffmpeg"):
        raise HTTPException(503, "Reconstruisez l’image Docker pour installer yt-dlp et FFmpeg.")
    async with lock:
        if active:
            raise HTTPException(409, "Une diffusion est déjà en cours. Arrêtez-la avant de charger YouTube.")
        if any(zone not in {z["id"] for z in ZONES} for zone in body.zones):
            raise HTTPException(422, "Sélectionnez des zones valides.")
        session = {"zones": sorted(set(body.zones)), "bytes": 0, "stop": False,
                   "source": "YouTube", "kind": "youtube", "started": time.time(),
                   "multicast_address": MULTICAST}
        player = YouTubePlayer(session, CONFIG, SETTINGS_PATH.parent / "youtube", youtube_finished)
        active = session
        youtube_player = player
        player.start(url, body.loop)
    return player.snapshot()


@app.post("/api/youtube/control")
async def youtube_control(body: YouTubeControl, request: Request):
    same_origin(request)
    authorize(request)
    async with lock:
        if not active or active.get("kind") != "youtube" or not youtube_player:
            raise HTTPException(409, "Aucune lecture YouTube en cours.")
        if body.action == "loop":
            youtube_player.state["loop"] = body.loop
        else:
            try:
                youtube_player.navigate(1 if body.action == "next" else -1)
            except ValueError as exc:
                raise HTTPException(409, str(exc))
        return youtube_player.snapshot()


@app.post("/api/library/start")
async def library_start(body: LibraryStart, request: Request):
    global active, youtube_player
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
                   "source": "Bibliothèque", "kind": "youtube", "started": time.time(),
                   "multicast_address": MULTICAST}
        player = YouTubePlayer(session, CONFIG, SETTINGS_PATH.parent / "youtube", youtube_finished)
        active = session
        youtube_player = player
        player.start_tracks(tracks, body.loop)
    return player.snapshot()


@app.post("/api/youtube/order")
async def youtube_order(body: QueueOrder, request: Request):
    same_origin(request)
    authorize(request)
    async with lock:
        if not active or active.get("kind") != "youtube" or not youtube_player or not youtube_player.state["queue"]:
            raise HTTPException(409, "Aucune file en cours.")
        try:
            youtube_player.reorder(body.order)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return youtube_player.snapshot()


@app.delete("/api/library/{video_id}")
async def delete_library_track(video_id: str, request: Request):
    same_origin(request)
    authorize(request)
    async with lock:
        if active:
            raise HTTPException(409, "Arrêtez la diffusion avant de supprimer une piste.")
        try:
            audio_library().delete(video_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
    return {"ok": True}


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


@app.post("/api/stop")
async def stop(request: Request):
    same_origin(request)
    authorize(request)
    if active:
        active["stop"] = True
        if active.get("kind") == "youtube" and youtube_player:
            await youtube_player.stop()
    return {"ok": True}


@app.websocket("/api/live")
async def live(ws: WebSocket):
    global active
    if not websocket_session(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    session = None
    process = None
    try:
        settings = await asyncio.wait_for(ws.receive_json(), 10)
        selected = settings.get("zones", [])
        async with lock:
            allowed = {z["id"] for z in ZONES}
            if not selected or any(type(z) is not int or z not in allowed for z in selected):
                raise ValueError("Sélectionnez des zones valides.")
            if active:
                raise ValueError("Une diffusion est déjà en cours.")
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
            process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                                                          stdout=asyncio.subprocess.DEVNULL, env=env)
        elif CONFIG["mode"] != "simulation":
            raise ValueError("Mode audio inconnu.")
        await ws.send_json({"ready": True, "mode": CONFIG["mode"]})
        start = time.monotonic()
        while not session["stop"]:
            if not websocket_session(ws):
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
        try:
            await ws.close()
        except RuntimeError:
            pass
