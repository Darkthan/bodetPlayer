"""Server-side YouTube queue: bounded downloads, cancellable playback and repeat."""
import asyncio
import json
import os
import re
import secrets
import signal
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MAX_TRACKS = 100
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_LIBRARY_BYTES = 1024 * 1024 * 1024
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{1,150}$")


def youtube_url(value):
    """Accept only video/playlist IDs and reconstruct a canonical YouTube URL."""
    try:
        url = urlsplit(value.strip())
        if url.scheme not in ("http", "https") or url.username or url.password or url.port not in (None, 80, 443):
            raise ValueError
        if url.hostname not in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"):
            raise ValueError
        query = parse_qs(url.query)
        playlist = query.get("list", [""])[0]
        if playlist and PLAYLIST_ID.fullmatch(playlist):
            return "https://www.youtube.com/playlist?list=" + playlist
        video = url.path.strip("/") if url.hostname == "youtu.be" else query.get("v", [""])[0]
        if url.path.startswith(("/shorts/", "/embed/")):
            video = url.path.split("/")[2]
        if not VIDEO_ID.fullmatch(video):
            raise ValueError
        return "https://www.youtube.com/watch?v=" + video
    except (ValueError, IndexError):
        raise ValueError("Saisissez un lien YouTube vers une vidéo ou une playlist.")


def queue_from_info(info):
    entries = info.get("entries") if "entries" in info else [info]
    tracks = []
    for entry in (entries or [])[:MAX_TRACKS]:
        if not entry or not VIDEO_ID.fullmatch(str(entry.get("id", ""))):
            continue
        if entry.get("is_live") or entry.get("live_status") in ("is_live", "is_upcoming"):
            continue
        if entry.get("duration") and entry["duration"] > 3600:
            continue
        track = {"id": entry["id"], "title": str(entry.get("title") or entry["id"])[:200]}
        if entry.get("duration"):
            track["duration"] = entry["duration"]
        tracks.append(track)
    if not tracks:
        raise ValueError("Aucune piste lisible dans ce lien (vidéos publiques, hors direct, de moins d’une heure).")
    return tracks


class Library:
    def __init__(self, directory):
        self.directory = Path(directory)

    def get(self, video_id):
        if not VIDEO_ID.fullmatch(video_id):
            raise ValueError("Identifiant de piste invalide.")
        try:
            track = json.loads((self.directory / (video_id + ".json")).read_text(encoding="utf-8"))
            filename = track["filename"]
            if not re.fullmatch(re.escape(video_id) + r"\.(?:webm|m4a|mp3|ogg|opus|wav|aac|mp4)", filename):
                raise ValueError
            path = self.directory / filename
            if track["id"] != video_id or not path.is_file():
                raise ValueError
            return track, path
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError("Piste absente de la bibliothèque.")

    def list(self):
        tracks = []
        for manifest in self.directory.glob("*.json"):
            try:
                track, path = self.get(manifest.stem)
                tracks.append({"id": track["id"], "title": track["title"],
                               "size": path.stat().st_size, "downloaded_at": track["downloaded_at"]})
            except (ValueError, OSError, KeyError):
                continue
        return sorted(tracks, key=lambda t: t["downloaded_at"], reverse=True)

    def save(self, track, source):
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / (track["id"] + source.suffix)
        source.replace(target)
        info = dict(track, filename=target.name, downloaded_at=time.time())
        info.pop("queue_id", None)
        manifest = self.directory / (track["id"] + ".json")
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        temporary.replace(manifest)
        return target

    def delete(self, video_id):
        _, path = self.get(video_id)
        path.unlink()
        (self.directory / (video_id + ".json")).unlink()


async def terminate(process):
    if process is None or process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        await asyncio.wait_for(process.wait(), 3)
    except asyncio.TimeoutError:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        await process.wait()
    except ProcessLookupError:
        await process.wait()


async def spawn(*args, **kwargs):
    return await asyncio.create_subprocess_exec(*args, **kwargs, start_new_session=os.name == "posix")


def ytdlp_command():
    return [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-plugin-dirs",
            "--no-cache-dir", "--js-runtimes", "node", "--socket-timeout", "15",
            "--retries", "2", "--fragment-retries", "2", "--no-progress", "--no-warnings"]


async def command_output(command, timeout, directory=None):
    process = await spawn(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    output = asyncio.create_task(process.communicate())
    started = time.monotonic()
    try:
        while not output.done():
            if time.monotonic() - started > timeout:
                raise ValueError("YouTube ne répond pas dans le délai prévu. Réessayez ou passez au suivant.")
            if directory and sum(p.stat().st_size for p in directory.iterdir() if p.is_file()) > MAX_FILE_BYTES:
                raise ValueError("Cette piste dépasse la limite de téléchargement de 100 Mio.")
            await asyncio.wait({output}, timeout=0.2)
        stdout, stderr = await output
        if process.returncode:
            detail = stderr.decode("utf-8", errors="replace").strip()[-600:]
            raise ValueError("Erreur yt-dlp : " + (detail or "vidéo indisponible"))
        return stdout
    finally:
        await terminate(process)
        if not output.done():
            output.cancel()
        await asyncio.gather(output, return_exceptions=True)


class YouTubePlayer:
    def __init__(self, session, config, directory, finished):
        self.session = session
        self.config = dict(config)
        self.directory = Path(directory)
        self.finished = finished
        self.state = {"status": "loading", "queue": [], "index": 0, "loop": "off",
                      "title": "Chargement YouTube", "error": "", "warning": ""}
        self.changed = asyncio.Event()
        self.task = None
        self.library = Library(self.directory / "library")
        self.track_bytes = 0

    def snapshot(self):
        return dict(self.state, elapsed=self.track_bytes / 96000)

    def start(self, url, repeat):
        self.state["loop"] = repeat
        self.task = asyncio.create_task(self.run(url))

    def start_tracks(self, tracks, repeat):
        self.state["loop"] = repeat
        self.task = asyncio.create_task(self.run(tracks=tracks))

    def reorder(self, order):
        queue = self.state["queue"]
        by_id = {track["queue_id"]: track for track in queue}
        if len(order) != len(queue) or len(set(order)) != len(order) or set(order) != set(by_id):
            raise ValueError("La file a changé. Rechargez-la avant de modifier l’ordre.")
        current = queue[self.state["index"]]["queue_id"]
        self.state["queue"] = [by_id[key] for key in order]
        self.state["index"] = order.index(current)

    def navigate(self, offset):
        if not self.state["queue"] or self.state["status"] in ("stopped", "error"):
            raise ValueError("Aucune file YouTube en cours.")
        self.state["index"] = (self.state["index"] + offset) % len(self.state["queue"])
        self.changed.set()

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        # Also releases a reservation cancelled before the task first ran.
        if self.state["status"] not in ("stopped", "error"):
            self.state["status"] = "stopped"
        await self.finished(self.session)

    async def download(self, track, root):
        video_id = track["id"]
        try:
            _, path = self.library.get(video_id)
            return path
        except ValueError:
            pass
        if sum(p.stat().st_size for p in self.library.directory.glob("*") if p.is_file()) > MAX_LIBRARY_BYTES - MAX_FILE_BYTES:
            raise ValueError("Bibliothèque pleine (1 Gio). Supprimez des pistes téléchargées pour libérer de la place.")
        self.state["status"] = "downloading"
        with tempfile.TemporaryDirectory(prefix="download-", dir=root) as temporary:
            folder = Path(temporary)
            await command_output(ytdlp_command() + [
                "--no-playlist", "--abort-on-error", "-f", "bestaudio",
                "--max-filesize", str(MAX_FILE_BYTES), "--match-filter", "!is_live & duration <= 3600",
                "-o", str(folder / "audio.%(ext)s"), "--", "https://www.youtube.com/watch?v=" + video_id,
            ], 300, directory=folder)
            files = [p for p in folder.glob("audio.*") if p.suffix not in (".part", ".ytdl") and p.stat().st_size]
            if len(files) != 1 or files[0].stat().st_size > MAX_FILE_BYTES:
                raise ValueError("Piste indisponible, trop longue ou trop volumineuse.")
            target = self.library.save(track, files[0])
        return target

    async def play(self, path, root):
        decoder = bridge = None
        # Logs go to files to avoid stderr pipe backpressure while forwarding PCM.
        with tempfile.TemporaryFile(dir=root) as errors:
            try:
                if self.config["mode"] in ("bodet", "bridge"):
                    command = ([sys.executable, "-m", "app.bodet_bridge"] if self.config["mode"] == "bodet"
                               else self.config.get("bridge_command", []))
                    if not command or not all(isinstance(arg, str) for arg in command):
                        raise ValueError("Passerelle audio non configurée.")
                    env = dict(os.environ, BODET_ZONES=json.dumps(self.session["zones"]),
                               BODET_MULTICAST_ADDRESS=self.session["multicast_address"],
                               BODET_QUALITY=self.config.get("bodet_quality", "low"),
                               BODET_INTERFACE=self.config.get("bodet_interface", ""),
                               AUDIO_FORMAT="s16le", AUDIO_RATE="48000", AUDIO_CHANNELS="1")
                    bridge = await spawn(*command, stdin=asyncio.subprocess.PIPE,
                                         stdout=asyncio.subprocess.DEVNULL, stderr=errors, env=env)
                elif self.config["mode"] != "simulation":
                    raise ValueError("Mode audio inconnu.")
                decoder = await spawn("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                                      "-re", "-i", str(path), "-vn", "-ac", "1", "-ar", "48000",
                                      "-f", "s16le", "pipe:1", stdout=asyncio.subprocess.PIPE, stderr=errors)
                self.state["status"] = "playing"
                self.track_bytes = 0
                while data := await decoder.stdout.read(1920):
                    self.session["bytes"] += len(data)
                    self.track_bytes += len(data)
                    if bridge:
                        if bridge.returncode is not None:
                            raise ValueError("La passerelle audio s’est arrêtée.")
                        bridge.stdin.write(data)
                        await asyncio.wait_for(bridge.stdin.drain(), 3)
                if await decoder.wait():
                    raise ValueError("Le fichier YouTube ne peut pas être décodé.")
                if bridge:
                    bridge.stdin.close()
                    if await asyncio.wait_for(bridge.wait(), 5):
                        raise ValueError("Échec de la diffusion vers les enceintes.")
            except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError) as exc:
                raise ValueError("La passerelle audio ne reçoit plus le son.") from exc
            finally:
                await terminate(decoder)
                await terminate(bridge)
                errors.seek(0)
                detail = errors.read().decode("utf-8", errors="replace").strip()[-600:]
                if detail:
                    print("YouTube audio: " + detail, file=sys.stderr)

    async def item(self, track, root):
        path = await self.download(track, root)
        await self.play(path, root)

    async def run(self, url=None, tracks=None):
        item_task = event_task = None
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = tempfile.TemporaryDirectory(prefix="youtube-", dir=self.directory)
            root = Path(temporary.name)
            if tracks is None:
                info = json.loads(await command_output(ytdlp_command() + [
                    "--flat-playlist", "--playlist-end", str(MAX_TRACKS), "--dump-single-json", "--", url,
                ], 90))
                tracks = queue_from_info(info)
            self.state["queue"] = [dict(track, queue_id=secrets.token_hex(8)) for track in tracks]
            failures = 0
            while True:
                self.changed.clear()
                index = self.state["index"]
                track = self.state["queue"][index]
                self.track_bytes = 0
                self.state["title"] = track["title"]
                self.session["source"] = "YouTube · " + track["title"]
                item_task = asyncio.create_task(self.item(track, root))
                event_task = asyncio.create_task(self.changed.wait())
                done, _ = await asyncio.wait({item_task, event_task}, return_when=asyncio.FIRST_COMPLETED)
                if event_task in done:
                    item_task.cancel()
                    await asyncio.gather(item_task, return_exceptions=True)
                    failures = 0
                    continue
                event_task.cancel()
                await asyncio.gather(event_task, return_exceptions=True)
                failed = False
                try:
                    await item_task
                    failures = 0
                    self.state["warning"] = ""
                except (ValueError, OSError) as exc:
                    failures += 1
                    failed = True
                    self.state["warning"] = f"{track['title']} : {exc}"
                    if failures >= len(self.state["queue"]):
                        raise ValueError(self.state["warning"])
                # A navigation request arriving during cleanup takes precedence.
                if self.changed.is_set():
                    continue
                index = self.state["index"]
                if self.state["loop"] == "track" and not failed:
                    continue
                if index + 1 < len(self.state["queue"]):
                    self.state["index"] = index + 1
                elif self.state["loop"] == "playlist":
                    self.state["index"] = 0
                else:
                    if failed:
                        raise ValueError(self.state["warning"])
                    break
            self.state["status"] = "stopped"
        except asyncio.CancelledError:
            self.state["status"] = "stopped"
        except (ValueError, OSError) as exc:
            self.state["status"] = "error"
            self.state["error"] = str(exc)
        finally:
            for task in (item_task, event_task):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in (item_task, event_task) if t), return_exceptions=True)
            try:
                if temporary:
                    temporary.cleanup()
            finally:
                await self.finished(self.session)
