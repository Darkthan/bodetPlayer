import os
import urllib.request

port = os.getenv("PLAYER_PORT", "8080")
urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3).close()
