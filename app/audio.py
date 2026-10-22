"""Shared audio block and backlog limits for capture and server playback."""
import struct


def apply_volume(data, volume):
    """Scale signed little-endian PCM16 before encoding or speaker playback."""
    if volume == 100:
        return data
    if volume == 0:
        return bytes(len(data))
    gain = volume / 100
    return b''.join(struct.pack('<h', int(sample * gain))
                    for (sample,) in struct.iter_unpack('<h', data))


def audio_settings(values=None):
    try:
        # Fixed transport profile. Old environment variables and persisted
        # presets must not silently reactivate configurable buffer behavior.
        block = int(values["block_ms"] if values is not None else 20)
        backlog = int(values["max_backlog_ms"] if values is not None else 200)
    except ValueError:
        raise ValueError("Les réglages de buffer audio doivent être des entiers en millisecondes.")
    if not 10 <= block <= 100:
        raise ValueError("PLAYER_AUDIO_BLOCK_MS doit être compris entre 10 et 100 ms.")
    if not max(40, 2 * block) <= backlog <= 1000:
        raise ValueError("PLAYER_AUDIO_MAX_BACKLOG_MS doit être entre 40 et 1000 ms et au moins deux fois la taille du bloc.")
    return {"block_ms": block, "max_backlog_ms": backlog,
            "pcm_bytes": block * 96, "max_backlog_bytes": backlog * 96}
