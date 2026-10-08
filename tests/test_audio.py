import pytest

from app.audio import audio_settings
from app.bodet_bridge import next_deadline


@pytest.mark.parametrize('block,backlog,expected', [(10, 100, 960), (20, 200, 1920), (100, 1000, 9600)])
def test_audio_buffer_settings(block, backlog, expected, monkeypatch):
    monkeypatch.setenv('PLAYER_AUDIO_BLOCK_MS', str(block))
    monkeypatch.setenv('PLAYER_AUDIO_MAX_BACKLOG_MS', str(backlog))
    settings = audio_settings()
    assert settings['pcm_bytes'] == expected
    assert settings['max_backlog_bytes'] == backlog * 96


@pytest.mark.parametrize('block,backlog', [('bad', '200'), ('9', '200'), ('101', '200'),
                                         ('20', '39'), ('100', '100'), ('20', '1001')])
def test_invalid_audio_buffer_configuration(block, backlog, monkeypatch):
    monkeypatch.setenv('PLAYER_AUDIO_BLOCK_MS', block)
    monkeypatch.setenv('PLAYER_AUDIO_MAX_BACKLOG_MS', backlog)
    with pytest.raises(ValueError):
        audio_settings()


def test_packet_clock_does_not_accumulate_scheduler_latency():
    deadline = None
    for index in range(500):
        # Two milliseconds of scheduling jitter per block must not turn into
        # an extra second of queued audio after 500 blocks.
        now = 0.0 if deadline is None else deadline + 0.002
        deadline = next_deadline(deadline, now, 0.02, 0.02)
    assert deadline == pytest.approx(10.0)


def test_packet_clock_caps_catchup_after_interruption():
    assert next_deadline(1.0, 5.0, 0.02, 0.02) == pytest.approx(5.0)
