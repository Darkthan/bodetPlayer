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
        download = client.get(origin + '/api/agents/download', params={'server':origin})
        assert download.ok and download.content[-16:] == b'BODET_CONFIG_V1!'
        personalized = tmp_path / 'BodetAgent.exe'
        personalized.write_bytes(download.content)
        agent = subprocess.Popen([str(personalized), '--bootstrap-integration-test'], creationflags=subprocess.CREATE_NO_WINDOW)
        status = lambda: client.get(origin + '/api/status', timeout=2).json()
        wait_for(lambda: any(a['online'] for a in status()['agents']))
        agent_id = status()['agents'][0]['id']
        def bind_local():
            challenge = client.post(origin + '/api/agents/local/challenge', json={}, headers=headers).json()['challenge']
            assert requests.get('http://127.0.0.1:17861/identity', params={'challenge':challenge}, headers={'origin':'https://other.example'}, timeout=2).status_code == 403
            identity = requests.get('http://127.0.0.1:17861/identity', params={'challenge':challenge}, headers=headers, timeout=2)
            assert identity.headers['access-control-allow-origin'] == origin
            response = client.post(origin + '/api/agents/local', json=identity.json(), headers=headers)
            assert response.ok and response.json()['id'] == agent_id
        bind_local()
        if os.getenv('PLAYER_NATIVE_BROWSER_TEST') == '1':
            browser = subprocess.run(['node', str(Path(__file__).with_name('native-agent-browser.cjs'))],
                                     env=dict(os.environ, BODET_TEST_ORIGIN=origin, BODET_TEST_PASSWORD=env['PLAYER_PASSWORD']),
                                     capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            assert browser.returncode == 0, browser.stdout + browser.stderr
        # A reused personalized download cannot enroll a second PC.
        duplicate = subprocess.run([str(personalized), '--bootstrap-integration-test'], timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        assert duplicate.returncode == 1
        assert client.post(origin + f'/api/agents/{agent_id}/start', json={'zones':[1]}, headers=headers).ok
        wait_for(lambda: status()['active'] and status()['active']['bytes'] > 9600)
        assert status()['active']['kind'] == 'agent'
        assert client.post(origin + '/api/stop', json={}, headers=headers).ok
        wait_for(lambda: status()['active'] is None)
        # The same executable must survive a server outage and reconnect without pairing.
        server.terminate(); server.wait(timeout=10)
        time.sleep(1)
        assert agent.poll() is None
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)],
                                  env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        wait_for(lambda: client.get(origin + '/health', timeout=1).ok)
        assert client.post(origin + '/api/login', json={'password':env['PLAYER_PASSWORD']}, headers=headers).ok
        wait_for(lambda: any(a['online'] and a['id'] == agent_id for a in status()['agents']), seconds=25)
        assert status()['active'] is None
        # Stay idle longer than the liveness deadline: heartbeats keep the connection alive.
        time.sleep(16)
        assert agent.poll() is None
        assert any(a['online'] and a['id'] == agent_id for a in status()['agents'])
        bind_local()
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
