import asyncio
import json
import shutil
import socket
import wave

import pytest

from app import playback

TRACKS = [{'id':'abcdefghijk', 'title':'A'}, {'id':'12345678901', 'title':'B'}]


def player(tmp_path):
    session = {"zones": [1], "multicast_address": "239.1.2.3", "bytes": 0}
    finished = []

    async def done(session):
        finished.append(session)

    return playback.AudioPlayer(session, {"mode": "simulation"}, tmp_path, done), finished


def test_queue_advances_and_finishes_without_repeat(tmp_path, monkeypatch):
    p, finished = player(tmp_path)
    played = []

    async def item(track, root):
        played.append(track["id"])

    monkeypatch.setattr(p, "item", item)
    asyncio.run(p.run(TRACKS))
    assert played == ["abcdefghijk", "12345678901"]
    assert p.state["status"] == "stopped" and finished
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("repeat, expected", [
    ("track", ["abcdefghijk"] * 3),
    ("playlist", ["abcdefghijk", "12345678901", "abcdefghijk"]),
])
def test_repeat_modes(tmp_path, monkeypatch, repeat, expected):
    p, finished = player(tmp_path)
    played = []

    async def item(track, root):
        played.append(track["id"])
        if len(played) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(p, "item", item)
    p.state["loop"] = repeat
    asyncio.run(p.run(TRACKS))
    assert played == expected
    assert finished


def test_navigation_cancels_current_track_and_stop_cleans_up(tmp_path, monkeypatch):
    p, finished = player(tmp_path)
    played, cancelled = [], []

    async def exercise():
        entered = asyncio.Queue()

        async def item(track, root):
            played.append(track["id"])
            await entered.put(track["id"])
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(track["id"])

        monkeypatch.setattr(p, "item", item)
        p.start_tracks(TRACKS, "track")
        assert await asyncio.wait_for(entered.get(), 2) == "abcdefghijk"
        p.navigate(1)
        assert await asyncio.wait_for(entered.get(), 2) == "12345678901"
        p.navigate(-1)
        assert await asyncio.wait_for(entered.get(), 2) == "abcdefghijk"
        await p.stop()

    asyncio.run(exercise())
    assert played == cancelled == ["abcdefghijk", "12345678901", "abcdefghijk"]
    assert p.state["status"] == "stopped" and finished
    assert not list(tmp_path.iterdir())


def test_all_unavailable_tracks_report_error_without_infinite_repeat(tmp_path, monkeypatch):
    p, finished = player(tmp_path)

    async def item(track, root):
        raise ValueError("Piste indisponible")

    monkeypatch.setattr(p, "item", item)
    p.state["loop"] = "playlist"
    asyncio.run(p.run(TRACKS))
    assert p.state["status"] == "error"
    assert "indisponible" in p.state["error"] and finished


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_local_audio_decodes_in_realtime_without_browser(tmp_path):
    p, _ = player(tmp_path)
    path = tmp_path / "source.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b"\x00\x00" * 12000)
    asyncio.run(p.play(path, tmp_path))
    assert p.session["bytes"] == 24000


def test_stop_before_runner_starts_releases_reservation(tmp_path):
    p, finished = player(tmp_path)

    async def exercise():
        p.start_tracks(TRACKS, "off")
        await p.stop()

    asyncio.run(exercise())
    assert p.state["status"] == "stopped" and finished


def test_reorder_preserves_current_track_and_changes_natural_successor(tmp_path, monkeypatch):
    p, _ = player(tmp_path)
    played = []

    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def item(track, root):
            played.append(track['title'])
            if track['title'] == 'A':
                entered.set()
                await release.wait()

        monkeypatch.setattr(p, 'item', item)
        p.start_tracks(TRACKS + [{'id':'12345678902', 'title':'C'}], 'off')
        await asyncio.wait_for(entered.wait(), 2)
        queue = p.state['queue']
        p.reorder([queue[1]['queue_id'], queue[0]['queue_id'], queue[2]['queue_id']])
        assert p.state['index'] == 1
        with pytest.raises(ValueError):
            p.reorder([queue[0]['queue_id']] * 3)
        release.set()
        await asyncio.wait_for(p.task, 2)

    asyncio.run(exercise())
    assert played == ['A', 'C']


def test_library_survives_new_player_and_rejects_path_traversal(tmp_path):
    p, _ = player(tmp_path)
    source = tmp_path / 'source.webm'
    source.write_bytes(b'audio')
    p.library.save({'id': 'abcdefghijk', 'title': 'Saved'}, source)
    new_player, _ = player(tmp_path)
    assert new_player.library.list()[0]['title'] == 'Saved'
    _, path = new_player.library.get('abcdefghijk')
    assert path.read_bytes() == b'audio'
    manifest = new_player.library.directory / 'abcdefghijk.json'
    manifest.write_text(json.dumps({'id': 'abcdefghijk', 'filename': '../source.webm'}))
    with pytest.raises(ValueError):
        new_player.library.get('abcdefghijk')


@pytest.mark.skipif(playback.os.name != 'posix' or not shutil.which('ffmpeg'), reason='Linux FFmpeg integration')
def test_server_decoding_and_native_bridge_emit_multicast_on_loopback(tmp_path):
    p, _ = player(tmp_path)
    p.config.update(mode='bodet', bodet_interface='127.0.0.1')
    path = tmp_path / 'source.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b'\x00\x00' * 24000)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('', 1681))
        listener.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                            socket.inet_aton('239.1.2.3') + socket.inet_aton('127.0.0.1'))
        listener.settimeout(2)
        asyncio.run(p.play(path, tmp_path))
        datagram, _ = listener.recvfrom(2048)
        assert datagram.startswith(b'MEL')
        assert p.session['bytes'] == 48000
