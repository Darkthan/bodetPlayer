import os
import uvicorn


def server_options():
    port = int(os.getenv("PLAYER_PORT", "8080"))
    if not 1024 <= port <= 65535:
        raise ValueError("PLAYER_PORT doit être compris entre 1024 et 65535.")
    options = {"host": "0.0.0.0", "port": port, "ws_max_size": 65536, "proxy_headers": False}
    return options


if __name__ == "__main__":
    uvicorn.run("app.main:app", **server_options())
