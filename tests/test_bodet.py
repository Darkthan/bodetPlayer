import io
import json
import struct
import shutil
import tempfile
import os
import threading
import time

import pytest

from app import bodet_bridge


def test_packet_reference_fields_and_checksum():
    message = bodet_bridge.packet(256, [1, 8, 9, 100], b"\xff\xfb\x00")
    assert message[:11] == b"MEL\x00\x24\x01\x00\x00\xff\x07\x03"
    assert message[11:24] == bytes([129, 1] + [0] * 10 + [8])
    assert message[24:31] == b"\x0c\x11\x05\x08\x05\x03\xe8"
    assert struct.unpack(">H", message[3:5])[0] == len(message)
    checksum = 0
    for i, byte in enumerate(message[:-2]):
        checksum ^= i + byte
    assert message[-2:] == checksum.to_bytes(2, "big")


@pytest.mark.parametrize("zones", [[], [0], [101], [True]])
def test_invalid_zone(zones):
    with pytest.raises(ValueError):
        bodet_bridge.packet(0, zones, b"audio")


@pytest.mark.parametrize("block", [10, 20, 40, 100])
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_real_encoder_with_intercepted_datagrams(monkeypatch, block):
    sent = []

    class Sender:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def sendto(self, data, target):
            sent.append((data, target))

    monkeypatch.setenv("BODET_MULTICAST_ADDRESS", "239.192.55.1")
    monkeypatch.setenv("BODET_ZONES", json.dumps([1, 100]))
    monkeypatch.setenv("BODET_QUALITY", "low")
    monkeypatch.setenv("BODET_INTERFACE", "")
    monkeypatch.setenv("PLAYER_AUDIO_BLOCK_MS", str(block))
    monkeypatch.setenv("PLAYER_AUDIO_MAX_BACKLOG_MS", "1000")
    monkeypatch.setattr(bodet_bridge.socket, "socket", lambda *args: Sender())
    monkeypatch.setattr(bodet_bridge.signal, "signal", lambda *args: None)
    with tempfile.TemporaryFile() as source:
        source.write(b"\x00\x00" * 24000)
        source.seek(0)
        monkeypatch.setattr(bodet_bridge.sys, "stdin", io.TextIOWrapper(source))
        bodet_bridge.main()
    assert sent and len(sent) % 2 == 0
    for index in range(0, len(sent), 2):
        assert sent[index] == sent[index + 1]
        message, target = sent[index]
        assert target == ("239.192.55.1", 1681)
        assert message[7] == (index // 2) % 256
    mp3 = b"".join(sent[i][0][31:-2] for i in range(0, len(sent), 2))
    assert mp3[:2] in (b"\xff\xfb", b"\xff\xfa")


@pytest.mark.parametrize("block", [10, 20, 40, 100])
def test_capture_buffer_does_not_fragment_bodet_packets(monkeypatch, block):
    sent = []
    encoded = bytes(range(256)) * 3

    class Output(io.BytesIO):
        def read1(self, size):
            assert size == 1000
            return super().read1(size)

    class Encoder:
        stdout = Output(encoded)
        def wait(self, timeout):
            return 0
        def poll(self):
            return 0

    class Sender:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def setsockopt(self, *args):
            pass
        def sendto(self, data, target):
            sent.append(data)

    monkeypatch.setenv("BODET_MULTICAST_ADDRESS", "239.192.55.1")
    monkeypatch.setenv("BODET_ZONES", "[1]")
    monkeypatch.setenv("BODET_QUALITY", "low")
    monkeypatch.setenv("BODET_INTERFACE", "")
    monkeypatch.setenv("PLAYER_AUDIO_BLOCK_MS", str(block))
    monkeypatch.setenv("PLAYER_AUDIO_MAX_BACKLOG_MS", "1000")
    monkeypatch.setattr(bodet_bridge.subprocess, "Popen", lambda *args, **kwargs: Encoder())
    monkeypatch.setattr(bodet_bridge.socket, "socket", lambda *args: Sender())
    monkeypatch.setattr(bodet_bridge.signal, "signal", lambda *args: None)
    monkeypatch.setattr(bodet_bridge.time, "sleep", lambda duration: None)
    assert len(encoded) < 1000
    bodet_bridge.main()
    assert len(sent) == 2
    assert sent[0] == sent[1]
    assert sent[0][31:-2] == encoded


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_live_encoder_sends_before_source_finishes(monkeypatch):
    first_packet = []

    class Sender:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def sendto(self, data, target):
            if not first_packet:
                first_packet.append(time.monotonic())

    monkeypatch.setenv("BODET_MULTICAST_ADDRESS", "239.192.55.1")
    monkeypatch.setenv("BODET_ZONES", "[1]")
    monkeypatch.setenv("BODET_QUALITY", "low")
    monkeypatch.setenv("BODET_INTERFACE", "")
    monkeypatch.setattr(bodet_bridge.socket, "socket", lambda *args: Sender())
    monkeypatch.setattr(bodet_bridge.signal, "signal", lambda *args: None)
    read_fd, write_fd = os.pipe()
    started = time.monotonic()

    def feed():
        with os.fdopen(write_fd, "wb", buffering=0) as output:
            for _ in range(75):
                output.write(b"\x00\x00" * 960)
                time.sleep(0.02)

    feeder = threading.Thread(target=feed)
    with os.fdopen(read_fd, "rb") as source:
        monkeypatch.setattr(bodet_bridge.sys, "stdin", io.TextIOWrapper(source))
        feeder.start()
        try:
            bodet_bridge.main()
        finally:
            feeder.join(timeout=5)
    assert first_packet, "No audio emitted"
    # A live stream must start promptly, rather than waiting for input EOF
    # or FFmpeg's default multi-second stream analysis.
    assert first_packet[0] - started < 1.0
