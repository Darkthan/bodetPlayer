import asyncio
import json
import time
import os
import hashlib
import hmac
import struct
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
os.environ['PLAYER_PASSWORD'] = 'test-password'
os.environ['PLAYER_ORIGIN'] = 'https://testserver'
from app import main


@pytest.fixture(autouse=True)
def isolated_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'SETTINGS_PATH', tmp_path / 'settings.json')
    monkeypatch.setattr(main, 'AGENTS', main.AgentRegistry(lambda: main.SETTINGS_PATH))
    monkeypatch.setattr(main, 'active', None)
    monkeypatch.setattr(main, 'music_player', None)
    monkeypatch.setitem(main.CONFIG, 'mode', 'simulation')
    monkeypatch.setattr(main, 'ZONES', [{'id': 1, 'name': 'Salle'}])
    main.attempts.clear()
    main.local_agents.clear(); main.agent_challenges.clear()


def client():
    c = TestClient(main.app, base_url='https://testserver')
    assert c.post('/api/login', json={'password': 'test-password'}, headers={'origin': 'https://testserver'}).status_code == 200
    return c


HEADERS = {'origin': 'https://testserver'}


def pair(c):
    code = c.post('/api/agents/pairing', json={}, headers=HEADERS).json()['code']
    response = c.post('/api/agents/pair', json={'name': 'PC musique', 'code': code})
    assert response.status_code == 200
    bind_local(c, response.json())
    return response.json(), code


def bind_local(c, agent):
    challenge = c.post('/api/agents/local/challenge', json={}, headers=HEADERS).json()['challenge']
    proof = hmac.new(hashlib.sha256(agent['token'].encode()).digest(), challenge.encode(), hashlib.sha256).hexdigest()
    response = c.post('/api/agents/local', json={'challenge':challenge, 'proof':proof}, headers=HEADERS)
    assert response.status_code == 200 and response.json()['id'] == agent['id']
    return {'challenge':challenge, 'proof':proof}


def test_pairing_expiry_authentication_and_persistence():
    with TestClient(main.app, base_url='https://testserver') as c:
        assert c.post('/api/agents/pairing', json={}, headers=HEADERS).status_code == 401
        assert c.get('/api/agents/download').status_code == 401
    with client() as c:
        assert c.post('/api/agents/pairing', json={}).status_code == 403
        agent, code = pair(c)
        assert c.post('/api/agents/pair', json={'name': 'Other', 'code': code}).status_code == 401
        registry = main.AgentRegistry(lambda: main.SETTINGS_PATH)
        assert registry.authenticate('Bearer ' + agent['token'])['id'] == agent['id']
        persisted = (main.SETTINGS_PATH.parent / 'agents.json').read_text()
        assert agent['token'] not in persisted
        snapshot = c.get('/api/status').json()['agents'][0]
        assert snapshot['online'] is False and 'token_hash' not in snapshot
        main.AGENTS.codes['EXPIRED'] = time.time() - 1
        assert c.post('/api/agents/pair', json={'name': 'Other', 'code': 'EXPIRED'}).status_code == 401
        assert c.post(f"/api/agents/{agent['id']}/start", json={'zones': [1]}, headers=HEADERS).status_code == 409


def test_agent_requires_web_command_then_relays_audio_and_stops():
    with client() as c:
        agent, _ = pair(c)
        auth = {'authorization': 'Bearer ' + agent['token']}
        with c.websocket_connect('/api/agents/control', headers=auth) as control:
            assert control.receive_json()['command'] == 'connected'
            control.send_json({'status': 'idle', 'heartbeat': True})
            assert control.receive_json()['command'] == 'heartbeat'
            with c.websocket_connect('/api/live', headers=auth) as unauthorized:
                unauthorized.send_json({'zones': [1]})
                assert 'error' in unauthorized.receive_json()
            assert c.get('/api/status').json()['active'] is None
            assert c.post(f"/api/agents/{agent['id']}/start", json={'zones': [999]}, headers=HEADERS).status_code == 422
            assert c.post(f"/api/agents/{agent['id']}/start", json={'zones': [1]}, headers=HEADERS).status_code == 200
            command = control.receive_json()
            assert command['command'] == 'start' and command['audio']['block_ms'] == main.AUDIO['block_ms']
            with c.websocket_connect('/api/live', headers=auth) as audio:
                audio.send_json({'agent_session': command['session']})
                assert audio.receive_json()['ready']
                assert c.get('/api/status').json()['active']['kind'] == 'agent'
                with c.websocket_connect('/api/live', headers=auth) as duplicate:
                    duplicate.send_json({'agent_session': command['session']})
                    assert 'error' in duplicate.receive_json()
                assert c.post('/api/library/start', json={'tracks':['abcdefghijk'], 'zones':[1]}, headers=HEADERS).status_code in (409, 503)
                assert c.post('/api/stop', json={}, headers=HEADERS).status_code == 200
                assert control.receive_json()['command'] == 'stop'
                audio.send_bytes(b'\x00\x00' * 480)
                assert audio.receive()['type'] == 'websocket.close'
            assert c.get('/api/status').json()['active'] is None


def test_disconnect_and_revoke_remove_capture_permissions():
    with client() as c:
        agent, _ = pair(c)
        auth = {'authorization': 'Bearer ' + agent['token']}
        with c.websocket_connect('/api/agents/control', headers=auth) as control:
            control.receive_json()
            assert c.post(f"/api/agents/{agent['id']}/start", json={'zones':[1]}, headers=HEADERS).status_code == 200
            control.receive_json()
        assert c.get('/api/status').json()['active'] is None
        assert c.delete(f"/api/agents/{agent['id']}", headers=HEADERS).status_code == 200
        assert main.AGENTS.authenticate(auth['authorization']) is None
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect('/api/agents/control', headers=auth):
                pass


def test_agent_error_releases_pending_start_and_rejects_stale_reports():
    with client() as c:
        agent, _ = pair(c)
        with c.websocket_connect('/api/agents/control', headers={'authorization': 'Bearer ' + agent['token']}) as control:
            control.receive_json()
            c.post(f"/api/agents/{agent['id']}/start", json={'zones':[1]}, headers=HEADERS)
            command = control.receive_json()
            control.send_json({'status': 'error', 'error': 'Old error', 'session': 'old'})
            # Force a report/processing barrier in the same socket via an audio handshake.
            with c.websocket_connect('/api/live', headers={'authorization':'Bearer '+agent['token']}) as audio:
                audio.send_json({'agent_session':command['session']})
                assert audio.receive_json()['ready']
                control.send_json({'status':'error', 'error':'No output device', 'session':command['session']})
                audio.send_bytes(b'\x00\x00' * 480)
                assert audio.receive()['type'] == 'websocket.close'
            assert c.get('/api/status').json()['active'] is None


def test_agent_download_is_served_as_attachment(tmp_path, monkeypatch):
    path = tmp_path / 'BodetAgent.exe'; path.write_bytes(b'MZ-test')
    monkeypatch.setenv('PLAYER_AGENT_DOWNLOAD', str(path))
    with client() as c:
        response = c.get('/api/agents/download')
        assert response.status_code == 200 and response.content.startswith(b'MZ-test')
        assert 'attachment' in response.headers['content-disposition']
        assert response.headers['cache-control'] == 'no-store'
        assert response.content[-16:] == b'BODET_CONFIG_V1!'
        length = struct.unpack('<I', response.content[-20:-16])[0]
        bootstrap = json.loads(response.content[-20-length:-20])
        assert bootstrap['Server'] == 'https://testserver'
        # Tickets survive a server restart, and can register exactly one PC.
        main.AGENTS = main.AgentRegistry(lambda: main.SETTINGS_PATH)
        agent = c.post('/api/agents/pair', json={'code':bootstrap['Code'], 'name':'PC initial'})
        assert agent.status_code == 200
        assert c.post('/api/agents/pair', json={'code':bootstrap['Code'], 'name':'Autre PC'}).status_code == 401
        assert bootstrap['Code'] not in (main.SETTINGS_PATH.parent / 'agent-installations.json').read_text()


def test_browser_can_only_control_its_local_agent_and_proofs_cannot_be_replayed():
    with client() as first, client() as second:
        one, _ = pair(first)
        two, _ = pair(second)
        assert first.cookies.get('session_https') != second.cookies.get('session_https')
        assert first.post(f"/api/agents/{two['id']}/start", json={'zones':[1]}, headers=HEADERS).status_code == 403
        assert first.delete(f"/api/agents/{two['id']}", headers=HEADERS).status_code == 403
        proof = bind_local(first, one)
        assert second.post('/api/agents/local', json=proof, headers=HEADERS).status_code == 403
        assert first.post('/api/agents/local', json=proof, headers=HEADERS).status_code == 403
        main.active = {'kind':'agent', 'agent_id':two['id'], 'stop':False}
        assert first.post('/api/stop', json={}, headers=HEADERS).status_code == 403
        assert main.active['stop'] is False
        main.active = None
