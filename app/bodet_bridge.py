"""Live PCM to Harmonys MEL/MP3, using the protocol described by sigma-caster.

Reference: https://git.teleco.ch/crt/be-a-sigma.git/tree/src/sigma-caster.rs
This is an independent implementation of its packet format, not a Bodet SDK.
"""
import ipaddress
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time
from app.audio import audio_settings


def next_deadline(previous, now, duration, max_lag):
    # Preserve the audio clock instead of adding every scheduling delay to it.
    # After a long source interruption, allow at most one block of catch-up.
    return max(previous if previous is not None else now, now - max_lag) + duration


def packet(sequence, zones, audio):
    mask = bytearray(13)
    if not zones or any(type(zone) is not int or not 1 <= zone <= 100 for zone in zones):
        raise ValueError("Zones Bodet invalides.")
    for zone in zones:
        mask[(zone - 1) // 8] |= 1 << ((zone - 1) % 8)
    if not 1 <= len(audio) <= 1000:
        raise ValueError("Bloc MP3 invalide.")
    payload = (b"\x01\x00" + bytes((sequence % 256, 255)) + b"\x07\x03"
               + mask + b"\x0c\x11\x05\x08\x05\x03\xe8" + audio)
    message = b"MEL" + struct.pack(">H", len(payload) + 7) + payload
    checksum = 0
    for index, value in enumerate(message):
        checksum ^= (index + value) & 65535
    return message + struct.pack(">H", checksum)


def main():
    address = os.environ["BODET_MULTICAST_ADDRESS"]
    if not ipaddress.IPv4Address(address).is_multicast:
        raise ValueError("Adresse multicast Bodet invalide.")
    zones = json.loads(os.environ["BODET_ZONES"])
    packet(0, zones, b"\x00")  # Validate before creating encoder or socket.
    quality = os.environ.get("BODET_QUALITY", "low")
    if quality not in ("low", "high"):
        raise ValueError("Qualité Bodet invalide.")
    rate, bitrate = (32000, 64000) if quality == "low" else (48000, 256000)
    settings = audio_settings()
    # Capture blocks are PCM transport settings, not MEL packet sizes.
    # Keep the original encoded-output packetization for receiver compatibility.
    mp3_bytes = 1000
    block_seconds = mp3_bytes * 8 / bitrate
    encoder = subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-probesize", "32", "-analyzeduration", "0",
        "-f", "s16le", "-ar", "48000", "-ac", "1", "-blocksize", str(settings["pcm_bytes"]), "-i", "pipe:0",
        "-ac", "1", "-ar", str(rate), "-c:a", "libmp3lame", "-b:a", str(bitrate),
        "-write_xing", "0", "-id3v2_version", "0", "-flush_packets", "1",
        "-f", "mp3", "pipe:1"], stdin=sys.stdin.buffer, stdout=subprocess.PIPE)

    def interrupted(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            interface = os.environ.get("BODET_INTERFACE", "")
            if interface:
                sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                  socket.inet_aton(str(ipaddress.IPv4Address(interface))))
            sequence = 0
            deadline = None
            while True:
                # Forward available output without waiting to fill 1000 bytes.
                # Do not split MP3 output according to the PCM capture setting.
                audio = encoder.stdout.read1(mp3_bytes)
                if not audio:
                    break
                now = time.monotonic()
                message = packet(sequence, zones, audio)
                sender.sendto(message, (address, 1681))
                sender.sendto(message, (address, 1681))
                sequence += 1
                deadline = next_deadline(deadline, now, len(audio) * 8 / bitrate, block_seconds)
                time.sleep(max(0, deadline - time.monotonic()))
        if encoder.wait(timeout=3):
            raise RuntimeError("Échec de l’encodage MP3.")
    finally:
        if encoder.poll() is None:
            encoder.terminate()
            try:
                encoder.wait(timeout=2)
            except subprocess.TimeoutExpired:
                encoder.kill()
                encoder.wait()
        encoder.stdout.close()


if __name__ == "__main__":
    main()
