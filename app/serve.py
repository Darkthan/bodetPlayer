import os
import uvicorn
from app.audio import audio_settings


def server_options():
    port = int(os.getenv("PLAYER_PORT", "8080"))
    if not 1024 <= port <= 65535:
        raise ValueError("PLAYER_PORT doit être compris entre 1024 et 65535.")
    audio = audio_settings()
    options = {"host": "0.0.0.0", "port": port, "ws_max_size": 65536, "proxy_headers": False,
               "ws_max_queue": audio["max_backlog_ms"] // audio["block_ms"]}
    return options


if __name__ == "__main__":
    uvicorn.run("app.main:app", **server_options())
