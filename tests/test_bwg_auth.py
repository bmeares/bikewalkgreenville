"""Passwordless sign-in: codes, rate limits, user creation, profile, sign-out.

SMTP, the token table and the Meerschaum users table are stubbed
(`bwg_fakes`); the code logic and `bwg_user` run for real.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import bwg_fakes
from bwg_fakes import FakeUsers, MemoryPipe, auth, bearer, install


@pytest.fixture
def env():
    users = FakeUsers(admins=['admin@example.com'])
    install(users)
    sent = []
    codes = MemoryPipe()
    with patch.object(auth, 'LOGIN_CODES_PIPE', codes), \
         patch.object(auth, '_send_code_email', lambda email, code: sent.append((email, code))), \
         patch.object(auth, '_mint_token', lambda username: f'mrsm-key:{username}'), \
         patch.object(auth, 'threading', SimpleNamespace(Thread=_InlineThread)):
        app = FastAPI(); auth.init_app(app)
        yield TestClient(app), users, sent, codes


class _InlineThread:
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def test_request_code_validates_and_rate_limits(env):
    client, _, sent, _ = env
    assert client.post('/bwg/auth/request-code', json={'email': 'nope'}).status_code == 400
    assert client.post('/bwg/auth/request-code', json={'email': 'a+b@example.com'}).status_code == 400
    for _ in range(3):
        r = client.post('/bwg/auth/request-code', json={'email': ' Rider@Example.com '})
        assert r.status_code == 200 and r.json()['ok'] is True
    assert client.post('/bwg/auth/request-code', json={'email': 'rider@example.com'}).status_code == 429
    assert [e for e, _ in sent] == ['rider@example.com'] * 3
    # Per-IP cap: 20 an hour across addresses (3 used above).
    for i in range(17):
        assert client.post('/bwg/auth/request-code', json={'email': f'r{i}@example.com'}).status_code == 200
    assert client.post('/bwg/auth/request-code', json={'email': 'last@example.com'}).status_code == 429


def test_verify_creates_user_and_token_then_profile_and_sign_out(env):
    client, users, sent, _ = env
    client.post('/bwg/auth/request-code', json={'email': 'new@example.com'})
    code = sent[-1][1]
    wrong = '000000' if code != '000000' else '111111'
    assert client.post('/bwg/auth/verify', json={'email': 'new@example.com', 'code': wrong}).status_code == 401
    assert 'new@example.com' not in users.users
    r = client.post('/bwg/auth/verify', json={'email': 'new@example.com', 'code': code})
    assert r.status_code == 200, r.text
    assert r.json() == {'token': 'mrsm-key:new@example.com', 'email': 'new@example.com',
                        'display_name': None, 'is_admin': False}
    created = users.users['new@example.com']
    assert created['type'] == 'user' and created['attributes'] == {'scopes': ['bwg'], 'bwg': {}}
    assert created['email'] == 'new@example.com'
    # A code signs in once.
    assert client.post('/bwg/auth/verify', json={'email': 'new@example.com', 'code': code}).status_code == 401

    me = bearer('new@example.com')
    assert client.get('/bwg/auth/me').status_code == 401
    r = client.put('/bwg/auth/me', headers=me, json={'display_name': 'Spoke Rider', 'disclaimer_version': 2,
                                                   'settings': {'places': [{'name': 'Home'}]}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['display_name'] == 'Spoke Rider' and body['disclaimer_version'] == 2
    assert body['disclaimer_accepted_at'] and body['settings'] == {'places': [{'name': 'Home'}]}
    assert users.users['new@example.com']['attributes']['scopes'] == ['bwg']
    assert client.put('/bwg/auth/me', headers=me, json={'display_name': 'x' * 41}).status_code == 400
    assert client.put('/bwg/auth/me', headers=me, json={'display_name': 'shithead'}).status_code == 400
    assert client.get('/bwg/auth/me', headers=me).json()['display_name'] == 'Spoke Rider'
    assert client.get('/bwg/auth/me', headers=bearer('admin@example.com')).json()['is_admin'] is True
    assert client.delete('/bwg/auth/token', headers=me).status_code == 200
    assert users.revoked == ['token-new@example.com']


def test_code_locks_after_five_wrong_attempts(env):
    client, _, sent, _ = env
    client.post('/bwg/auth/request-code', json={'email': 'x@example.com'})
    code = sent[-1][1]
    wrong = '000000' if code != '000000' else '111111'
    for _ in range(auth.CODE_MAX_ATTEMPTS):
        assert client.post('/bwg/auth/verify', json={'email': 'x@example.com', 'code': wrong}).status_code == 401
    assert client.post('/bwg/auth/verify', json={'email': 'x@example.com', 'code': code}).status_code == 401


def test_moderation_check_and_banned_writes():
    install(FakeUsers(banned=['b@example.com']))
    assert auth.moderation_check('Nice quiet path behind the library') == 'ok'
    assert auth.moderation_check('Scunthorpe classic crossing') == 'ok'
    assert auth.moderation_check('what the fuck') == 'held'
    assert auth.moderation_check('see http://a.example and www.b.example') == 'held'
    assert auth.moderation_check('THIS PATH IS TOTALLY CLOSED NOW') == 'held'
    assert [auth.moderation_check('Same text', username='a') for _ in range(4)] == ['ok', 'ok', 'ok', 'held']
    # The repeat counter is per user, and skipped without one (group-ride names).
    assert auth.moderation_check('Same text', username='b') == 'ok'
    assert [auth.moderation_check('Same text') for _ in range(5)] == ['ok'] * 5

    class Req:
        headers = {'authorization': 'Bearer mrsm-key:b@example.com'}
    from fastapi import HTTPException
    assert auth.bwg_user(Req())['banned'] is True
    with pytest.raises(HTTPException) as e:
        auth.require_user(Req())
    assert e.value.status_code == 403
    assert auth.require_user(Req(), write=False)['username'] == 'b@example.com'


def test_email_rejects_sql_comment_chars_and_daily_guess_cap(env):
    client, _, sent, codes = env
    for bad in ('a--b@example.com', 'a;b@example.com', "o'neil@example.com", 'a b@example.com'):
        r = client.post('/bwg/auth/request-code', json={'email': bad})
        assert r.status_code == 400 and 'quotes' in r.json()['error'], bad
    # 20 wrong guesses a day per email, across however many codes.
    codes.rows += [{'id': f'f{i}', 'kind': 'fail', 'email': 'g@example.com', 'code_id': 'old',
                    'ts': auth.datetime.now(auth.timezone.utc)} for i in range(auth.FAILS_PER_EMAIL[0])]
    client.post('/bwg/auth/request-code', json={'email': 'g@example.com'})
    r = client.post('/bwg/auth/verify', json={'email': 'g@example.com', 'code': sent[-1][1]})
    assert r.status_code == 429


def test_only_tokens_minted_here_are_honoured():
    import uuid
    users = FakeUsers(admins=['admin@example.com'])
    install(users)
    ours, foreign = str(uuid.uuid4()), str(uuid.uuid4())
    pipe = MemoryPipe([{'id': 'x', 'kind': 'token', 'email': 'r@example.com', 'code_id': ours}])
    tokens = {'mrsm-key:ours': ('r@example.com', ours), 'mrsm-key:foreign': ('r@example.com', foreign),
              'mrsm-key:admin': ('admin@example.com', foreign)}

    class Req:
        def __init__(self, key):
            self.headers = {'authorization': f'Bearer {key}'}
    with patch.object(auth, 'LOGIN_CODES_PIPE', pipe), \
         patch.object(auth, '_minted_token', bwg_fakes.real_minted_token), \
         patch.object(auth, '_resolve_token', tokens.get):
        assert auth.bwg_user(Req('mrsm-key:ours'))['username'] == 'r@example.com'
        assert auth.bwg_user(Req('mrsm-key:foreign')) is None
        assert auth.bwg_user(Req('mrsm-key:admin'))['is_admin'] is True


def test_validate_image_sniffs_magic_bytes():
    import io
    from fastapi import HTTPException
    up = lambda b: SimpleNamespace(file=io.BytesIO(b))  # noqa: E731
    assert auth.validate_image(up(bwg_fakes.JPEG)) == '.jpg'
    assert auth.validate_image(up(bwg_fakes.PNG)) == '.png'
    assert auth.validate_image(up(b'RIFF\x00\x00\x00\x00WEBPVP8 ')) == '.webp'
    f = up(b'<svg onload=alert(1)>')
    with pytest.raises(HTTPException) as e:
        auth.validate_image(f)
    assert e.value.status_code == 400
