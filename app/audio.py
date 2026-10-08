"""Shared audio block and backlog limits for capture and server playback."""
import os


def audio_settings():
    try:
        block = int(os.getenv("PLAYER_AUDIO_BLOCK_MS", "20"))
        backlog = int(os.getenv("PLAYER_AUDIO_MAX_BACKLOG_MS", "200"))
    except ValueError:
        raise ValueError("Les réglages de buffer audio doivent être des entiers en millisecondes.")
    if not 10 <= block <= 100:
        raise ValueError("PLAYER_AUDIO_BLOCK_MS doit être compris entre 10 et 100 ms.")
    if not max(40, 2 * block) <= backlog <= 1000:
        raise ValueError("PLAYER_AUDIO_MAX_BACKLOG_MS doit être entre 40 et 1000 ms et au moins deux fois la taille du bloc.")
    return {"block_ms": block, "max_backlog_ms": backlog,
            "pcm_bytes": block * 96, "max_backlog_bytes": backlog * 96}
