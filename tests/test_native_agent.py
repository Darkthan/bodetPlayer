"""Native executable -> real local server using a synthetic tone, never PC audio."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import pytest
import requests

EXE = Path(__file__).resolve().parents[1] / 'app' / 'downloads' / 'BodetAgent.exe'


@pytest.mark.skipif(sys.platform != 'win32' or not EXE.is_file(), reason='Compiled Windows agent unavailable')
def test_native_agent_web_control_and_pcm_stream(tmp_path):
    with socket.socket() as allocator:
        allocator.bind(('127.0.0.1', 0))
        port = allocator.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'mode':'simulation', 'zones':[{'id':1,'name':'Test'}]}))
    env = dict(os.environ, PLAYER_PASSWORD=secrets.token_urlsafe(16), PLAYER_ORIGIN=origin,
               PLAYER_CONFIG=str(config), PLAYER_SETTINGS=str(tmp_path / 'settings.json'))
    env['PLAYER_AUDIO_BLOCK_MS'] = '20'; env['PLAYER_AUDIO_MAX_BACKLOG_MS'] = '500'
    log = (tmp_path / 'server.log').open('w')
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)],
                              env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    agent = None
    client = requests.Session()
    headers = {'origin':origin}
    try:
        def wait_for(check, seconds=15):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    result = check()
                    if result:
                        return result
                except requests.RequestException:
                    pass
                time.sleep(.1)
            raise AssertionError('Timed out waiting for native agent/server')
        wait_for(lambda: client.get(origin + '/health', timeout=1).ok)
        assert client.post(origin + '/api/login', json={'password':env['PLAYER_PASSWORD']}, headers=headers).ok
        code = client.post(origin + '/api/agents/pairing', json={}, headers=headers).json()['code']
        agent = subprocess.Popen([str(EXE), '--integration-test', origin, code], creationflags=subprocess.CREATE_NO_WINDOW)
        status = lambda: client.get(origin + '/api/status', timeout=2).json()
        wait_for(lambda: any(a['online'] for a in status()['agents']))
        agent_id = status()['agents'][0]['id']
        assert client.post(origin + f'/api/agents/{agent_id}/start', json={'zones':[1]}, headers=headers).ok
        wait_for(lambda: status()['active'] and status()['active']['bytes'] > 9600)
        assert status()['active']['kind'] == 'agent'
        assert client.post(origin + '/api/stop', json={}, headers=headers).ok
        wait_for(lambda: status()['active'] is None)
        # Restart the same agent without repeating pairing, then revoke it while streaming.
        assert client.post(origin + f'/api/agents/{agent_id}/start', json={'zones':[1]}, headers=headers).ok
        wait_for(lambda: status()['active'] and status()['active']['bytes'] > 9600)
        assert client.delete(origin + f'/api/agents/{agent_id}', headers=headers).ok
        wait_for(lambda: status()['active'] is None)
        assert not status()['agents']
    finally:
        if agent is not None:
            agent.terminate(); agent.wait(timeout=10)
        server.terminate(); server.wait(timeout=10)
        log.close(); client.close()
