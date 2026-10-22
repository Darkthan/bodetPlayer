import pytest

from app.audio import audio_settings, apply_volume
from app.bodet_bridge import next_deadline


@pytest.mark.parametrize('volume,expected', [
    (100, [-32768, -10000, 0, 10000, 32767]),
    (50, [-16384, -5000, 0, 5000, 16383]),
    (0, [0, 0, 0, 0, 0]),
])
def test_volume_scales_pcm_samples_without_changing_block_size(volume, expected):
    import struct
    data = struct.pack('<5h', -32768, -10000, 0, 10000, 32767)
    result = apply_volume(data, volume)
    assert len(result) == len(data)
    assert list(struct.unpack('<5h', result)) == expected


@pytest.mark.parametrize('block,backlog,expected', [(10, 100, 960), (20, 200, 1920), (100, 1000, 9600)])
def test_audio_buffer_settings(block, backlog, expected, monkeypatch):
    monkeypatch.setenv('PLAYER_AUDIO_BLOCK_MS', str(block))
    monkeypatch.setenv('PLAYER_AUDIO_MAX_BACKLOG_MS', str(backlog))
    settings = audio_settings({"block_ms": block, "max_backlog_ms": backlog})
    assert settings['pcm_bytes'] == expected
    assert settings['max_backlog_bytes'] == backlog * 96


@pytest.mark.parametrize('block,backlog', [('bad', '200'), ('9', '200'), ('101', '200'),
                                         ('20', '39'), ('100', '100'), ('20', '1001')])
def test_invalid_audio_buffer_configuration(block, backlog, monkeypatch):
    monkeypatch.setenv('PLAYER_AUDIO_BLOCK_MS', block)
    monkeypatch.setenv('PLAYER_AUDIO_MAX_BACKLOG_MS', backlog)
    with pytest.raises(ValueError):
        audio_settings({"block_ms": block, "max_backlog_ms": backlog})


def test_fixed_audio_profile_ignores_legacy_environment(monkeypatch):
    monkeypatch.setenv('PLAYER_AUDIO_BLOCK_MS', '10')
    monkeypatch.setenv('PLAYER_AUDIO_MAX_BACKLOG_MS', 'invalid')
    assert audio_settings() == {'block_ms': 20, 'max_backlog_ms': 200,
                                'pcm_bytes': 1920, 'max_backlog_bytes': 19200}


@pytest.mark.parametrize('duration', [0.02, 0.036, 0.125])
def test_packet_clock_never_bursts_to_catch_up_after_jitter(duration):
    deadline = None
    now = 0.0
    for jitter in [0.002, 0.0, 0.03, 0.001, 1.0, 0.0] * 500:
        if deadline is not None:
            now = deadline + jitter
        deadline = next_deadline(deadline, now, duration)
        assert deadline - now == pytest.approx(duration)


def test_packet_clock_waits_full_interval_after_interruption():
    assert next_deadline(1.0, 5.0, 0.02) == pytest.approx(5.02)


def test_packet_clock_matches_pre_buffer_control_behavior():
    assert next_deadline(None, 1.0, 0.02) == pytest.approx(1.02)
    assert next_deadline(1.02, 1.022, 0.02) == pytest.approx(1.042)


def test_saved_buffer_preset_is_ignored(tmp_path, monkeypatch):
    from app import main
    path = tmp_path / 'settings.json'
    path.write_text('{"audio": {"block_ms": 100, "max_backlog_ms": 1000}}', encoding='utf-8')
    monkeypatch.setattr(main, 'SETTINGS_PATH', path)
    assert main.load_audio() == audio_settings()
