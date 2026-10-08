import os
import json
import pytest

os.environ['PLAYER_PASSWORD'] = 'test-password'
os.environ['PLAYER_ORIGIN'] = 'https://testserver'

from fastapi.testclient import TestClient
from app import main


@pytest.fixture(autouse=True)
def isolated_network_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'active', None)
    monkeypatch.setattr(main, 'youtube_player', None)
    monkeypatch.setitem(main.CONFIG, 'mode', 'simulation')
    monkeypatch.setattr(main, 'SETTINGS_PATH', tmp_path / 'settings.json')
    monkeypatch.setattr(main, 'MULTICAST', '239.192.55.1')
    monkeypatch.setattr(main, 'ZONES', [dict(z) for z in main.CONFIG['zones']])


def client():
    main.attempts.clear()
    result = TestClient(main.app, base_url='https://testserver')
    response = result.post('/api/login', json={'password': 'test-password'},
                           headers={'origin': 'https://testserver'})
    assert response.status_code == 200
    return result


def test_authentication_and_origin():
    main.attempts.clear()
    with TestClient(main.app, base_url='https://testserver') as c:
        assert c.get('/api/status').status_code == 401
        assert c.post('/api/login', json={'password': 'test-password'}).status_code == 403
        assert c.post('/api/login', json={'password': 'wrong'},
                      headers={'origin': 'https://testserver'}).status_code == 401
    with client() as c:
        assert c.get('/api/status').json()['mode'] == 'simulation'
        assert c.post('/api/stop', json={}).status_code == 403


def test_invalid_zone():
    with client() as c:
        with c.websocket_connect('wss://testserver/api/live', headers={'origin': 'https://testserver'}) as ws:
            ws.send_json({'zones': [999]})
            assert 'error' in ws.receive_json()
        assert c.get('/api/status').json()['active'] is None


def test_stream_exclusion_and_stop():
    with client() as c:
        with c.websocket_connect('wss://testserver/api/live', headers={'origin': 'https://testserver'}) as first:
            first.send_json({'zones': [1, 2], 'source': 'Test PC'})
            assert first.receive_json()['ready']
            assert c.get('/api/status').json()['active']['multicast_address'] == '239.192.55.1'
            assert c.post('/api/settings/network', json={'multicast_address': '239.1.2.3'},
                          headers={'origin': 'https://testserver'}).status_code == 409
            with c.websocket_connect('wss://testserver/api/live', headers={'origin': 'https://testserver'}) as second:
                second.send_json({'zones': [3]})
                assert 'déjà' in second.receive_json()['error']
            assert c.get('/api/status').json()['active']['zones'] == [1, 2]
            assert c.post('/api/stop', json={}, headers={'origin': 'https://testserver'}).status_code == 200
            # Unblock the receive loop and wait for the server's close frame.
            first.send_bytes(b'\x00\x00' * 128)
            assert first.receive()['type'] == 'websocket.close'
        assert c.get('/api/status').json()['active'] is None


def test_multicast_validation_and_persistence():
    with client() as c:
        for address in ['192.168.1.1', 'not-an-address', '240.1.2.3', 'ff02::1']:
            response = c.post('/api/settings/network', json={'multicast_address': address},
                              headers={'origin': 'https://testserver'})
            assert response.status_code == 422
        assert not main.SETTINGS_PATH.exists()
        response = c.post('/api/settings/network', json={'multicast_address': ' 239.10.20.30 '},
                          headers={'origin': 'https://testserver'})
        assert response.status_code == 200
        assert c.get('/api/status').json()['multicast_address'] == '239.10.20.30'
        assert json.loads(main.SETTINGS_PATH.read_text())['multicast_address'] == '239.10.20.30'
        assert main.load_multicast() == '239.10.20.30'


def test_multicast_configuration_requires_authentication_and_origin():
    with TestClient(main.app, base_url='https://testserver') as c:
        assert c.post('/api/settings/network', json={'multicast_address': '239.1.2.3'},
                      headers={'origin': 'https://testserver'}).status_code == 401
    with client() as c:
        assert c.post('/api/settings/network', json={'multicast_address': '239.1.2.3'}).status_code == 403


def test_invalid_audio_releases_session():
    with client() as c:
        with c.websocket_connect('wss://testserver/api/live', headers={'origin': 'https://testserver'}) as ws:
            ws.send_json({'zones': [1]})
            assert ws.receive_json()['ready']
            ws.send_bytes(b'\x00')
            assert 'invalide' in ws.receive_json()['error']
        assert c.get('/api/status').json()['active'] is None


def test_http_login_and_websocket(monkeypatch):
    origin = 'http://127.0.0.1:9080'
    monkeypatch.setattr(main, 'ORIGINS', frozenset([origin]))
    main.attempts.clear()
    with TestClient(main.app, base_url='http://127.0.0.1:9080') as c:
        response = c.post('/api/login', json={'password': 'test-password'},
                          headers={'origin': origin})
        assert response.status_code == 200
        assert 'Secure' not in response.headers['set-cookie']
        assert c.get('/api/status').status_code == 200
        with c.websocket_connect('ws://127.0.0.1:9080/api/live', headers={'origin': origin}) as ws:
            ws.send_json({'zones': [1]})
            assert ws.receive_json()['ready']


def test_native_server_port_http_only(monkeypatch):
    from app.serve import server_options
    monkeypatch.setenv('PLAYER_PORT', '9080')
    assert server_options()['port'] == 9080
    assert 'ssl_certfile' not in server_options()
    assert server_options()['proxy_headers'] is False
    monkeypatch.setenv('PLAYER_PORT', '70000')
    with pytest.raises(ValueError):
        server_options()


def test_zone_crud_and_combined_persistence():
    headers = {'origin': 'https://testserver'}
    with client() as c:
        assert c.post('/api/settings/zones', json={'id': 12, 'name': ' Salle 12 '}, headers=headers).status_code == 200
        assert c.post('/api/settings/zones', json={'id': 12, 'name': 'Doublon'}, headers=headers).status_code == 409
        assert c.post('/api/settings/zones', json={'id': 101, 'name': 'Invalide'}, headers=headers).status_code == 422
        assert c.post('/api/settings/zones', json={'id': 13, 'name': '  '}, headers=headers).status_code == 422
        assert c.put('/api/settings/zones/12', json={'id': 14, 'name': 'Bibliothèque'}, headers=headers).status_code == 200
        assert c.put('/api/settings/zones/14', json={'id': 1, 'name': 'Conflit'}, headers=headers).status_code == 409
        assert c.post('/api/settings/network', json={'multicast_address': '239.1.2.3'}, headers=headers).status_code == 200
        saved = json.loads(main.SETTINGS_PATH.read_text(encoding='utf-8'))
        assert {'id': 14, 'name': 'Bibliothèque'} in saved['zones']
        assert c.delete('/api/settings/zones/14', headers=headers).status_code == 200
        saved = json.loads(main.SETTINGS_PATH.read_text(encoding='utf-8'))
        assert saved['multicast_address'] == '239.1.2.3'
        assert not any(z['id'] == 14 for z in saved['zones'])
        assert c.delete('/api/settings/zones/14', headers=headers).status_code == 404


def test_zone_security_and_live_lock():
    headers = {'origin': 'https://testserver'}
    with TestClient(main.app, base_url='https://testserver') as c:
        assert c.delete('/api/settings/zones/1', headers=headers).status_code == 401
    with client() as c:
        assert c.delete('/api/settings/zones/1').status_code == 403
        with c.websocket_connect('wss://testserver/api/live', headers=headers) as ws:
            ws.send_json({'zones': [1]})
            assert ws.receive_json()['ready']
            assert c.delete('/api/settings/zones/1', headers=headers).status_code == 409
            assert c.post('/api/settings/zones', json={'id': 9, 'name': 'Salle'}, headers=headers).status_code == 409


def test_youtube_queue_controls_and_shared_exclusion(monkeypatch):
    import asyncio
    from app import youtube

    original_spec = main.importlib.util.find_spec
    monkeypatch.setattr(main.importlib.util, 'find_spec', lambda name: object() if name == 'yt_dlp' else original_spec(name))

    async def metadata(*args, **kwargs):
        return json.dumps({'entries': [{'id': 'abcdefghijk', 'title': 'A'},
                                      {'id': '12345678901', 'title': 'B'}]}).encode()

    async def item(self, track, root):
        self.state['status'] = 'playing'
        await asyncio.Event().wait()

    monkeypatch.setattr(youtube, 'command_output', metadata)
    monkeypatch.setattr(youtube.YouTubePlayer, 'item', item)
    headers = {'origin': 'https://testserver'}
    with client() as c:
        assert c.post('/api/youtube/start', json={'url': 'https://youtu.be/abcdefghijk', 'zones': [1]}, headers=headers).status_code == 200
        assert c.post('/api/youtube/start', json={'url': 'https://youtu.be/abcdefghijk', 'zones': [1]}, headers=headers).status_code == 409
        state = c.get('/api/status').json()['youtube']
        assert [track['title'] for track in state['queue']] == ['A', 'B']
        order = [track['queue_id'] for track in state['queue']][::-1]
        assert c.post('/api/youtube/order', json={'order': order}, headers=headers).json()['index'] == 1
        assert c.post('/api/youtube/order', json={'order': [order[0], order[0]]}, headers=headers).status_code == 409
        assert c.post('/api/youtube/control', json={'action': 'loop', 'loop': 'playlist'}, headers=headers).json()['loop'] == 'playlist'
        assert c.post('/api/youtube/control', json={'action': 'next'}, headers=headers).status_code == 200
        with c.websocket_connect('wss://testserver/api/live', headers=headers) as ws:
            ws.send_json({'zones': [1]})
            assert 'déjà' in ws.receive_json()['error']
        assert c.post('/api/stop', json={}, headers=headers).status_code == 200
        assert c.get('/api/status').json()['active'] is None


def test_youtube_security_and_validation():
    headers = {'origin': 'https://testserver'}
    body = {'url': 'https://youtu.be/abcdefghijk', 'zones': [1]}
    with TestClient(main.app, base_url='https://testserver') as c:
        assert c.post('/api/youtube/start', json=body, headers=headers).status_code == 401
    with client() as c:
        assert c.post('/api/youtube/start', json=body).status_code == 403
        assert c.post('/api/youtube/control', json={'action': 'next'}).status_code == 403
        assert c.post('/api/youtube/order', json={'order': ['x']}).status_code == 403
        assert c.post('/api/youtube/start', json=dict(body, url='http://127.0.0.1'), headers=headers).status_code == 422
        assert c.post('/api/youtube/start', json=dict(body, zones=[True]), headers=headers).status_code == 422


@pytest.mark.parametrize('value', ['*', 'ftp://player.test', 'https://player.test/path',
                                   'https://user:password@player.test', 'https://player.test?x=1',
                                   'https://player.test:99999', ', ,'])
def test_invalid_origin_configuration(value):
    with pytest.raises(ValueError, match='PLAYER_ORIGIN'):
        main.allowed_origins(value)


def test_multiple_origin_configuration_normalizes_browser_origins():
    assert main.allowed_origins(' http://PLAYER.test:80/, https://player.test:443, ,http://player.test:8080 ') == {
        'http://player.test', 'https://player.test', 'http://player.test:8080'}
    assert main.allowed_origins('http://[::1]:8080') == {'http://[::1]:8080'}


def test_http_and_https_origins_on_same_hostname_keep_separate_cookies(monkeypatch):
    monkeypatch.setattr(main, 'ORIGINS', main.allowed_origins('http://player.test,https://player.test'))
    main.attempts.clear()
    with TestClient(main.app, base_url='https://player.test') as c:
        for origin in ('https://player.test', 'http://player.test'):
            response = c.post(origin + '/api/login', json={'password': 'test-password'}, headers={'origin': origin})
            assert response.status_code == 200
            cookie = response.headers['set-cookie']
            assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
            assert ('Secure' in cookie) == origin.startswith('https://')
            assert cookie.startswith(main.session_cookie(origin) + '=')
            assert c.get(origin + '/api/status').status_code == 200
            scheme = 'wss' if origin.startswith('https://') else 'ws'
            with c.websocket_connect(scheme + '://player.test/api/live', headers={'origin': origin}) as ws:
                ws.send_json({'zones': [1]})
                assert ws.receive_json()['ready']
        assert {'session', 'session_https'} <= {cookie.name for cookie in c.cookies.jar}
        assert c.post('/api/stop', json={}, headers={'origin': 'https://evil.test'}).status_code == 403
        with pytest.raises(main.WebSocketDisconnect) as error:
            with c.websocket_connect('wss://player.test/api/live', headers={'origin': 'https://evil.test'}):
                pass
        assert error.value.code == 1008
        assert c.post('/api/logout', headers={'origin': 'https://player.test'}).status_code == 200
        assert c.get('https://player.test/api/status').status_code == 401
        assert c.get('http://player.test/api/status').status_code == 401


def test_library_persistence_playback_order_and_delete(tmp_path, monkeypatch):
    import asyncio
    from app import youtube
    library = main.audio_library()
    for video_id, title in [('abcdefghijk', 'A'), ('12345678901', 'B')]:
        source = tmp_path / 'audio.webm'
        source.write_bytes(b'audio')
        library.save({'id': video_id, 'title': title}, source)

    async def item(self, track, root):
        self.state['status'] = 'playing'
        await asyncio.Event().wait()

    monkeypatch.setattr(youtube.YouTubePlayer, 'item', item)
    headers = {'origin': 'https://testserver'}
    with client() as c:
        assert len(c.get('/api/status').json()['library']) == 2
        assert c.post('/api/library/start', json={'tracks': ['12345678901', 'abcdefghijk'], 'zones': [1]}, headers=headers).status_code == 200
        state = c.get('/api/status').json()['youtube']
        assert [track['title'] for track in state['queue']] == ['B', 'A']
        assert c.delete('/api/library/abcdefghijk', headers=headers).status_code == 409
        assert c.post('/api/stop', json={}, headers=headers).status_code == 200
        assert c.delete('/api/library/abcdefghijk').status_code == 403
        assert c.delete('/api/library/abcdefghijk', headers=headers).status_code == 200
        assert len(c.get('/api/status').json()['library']) == 1
        assert c.post('/api/library/start', json={'tracks': ['abcdefghijk'], 'zones': [1]}, headers=headers).status_code == 422
