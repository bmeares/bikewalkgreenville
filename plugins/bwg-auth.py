#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BWG accounts: passwordless email sign-in for the Bike Walk Greenville app.

Routes (mounted on the Meerschaum API, i.e. https://bwg.mrsm.io):

  POST   /bwg/auth/request-code  {email}          -> {ok, message}
  POST   /bwg/auth/verify        {email, code}    -> {token, email, display_name, is_admin}
  GET    /bwg/auth/me            (Bearer)         -> profile
  PUT    /bwg/auth/me            (Bearer) {display_name?, settings?, disclaimer_version?}
  DELETE /bwg/auth/token         (Bearer)         -> sign this device out

The code is emailed with the SAME SMTP config as walk-audit
(`plugins:walk-audit:smtp`). Username == email; the Meerschaum user gets a
random password and `attributes = {'scopes': ['bwg'], 'bwg': {...}}`, and the
device receives a long-lived Meerschaum API token scoped `bwg`. Meerschaum
admins are the moderators.

Other plugins share the auth helpers through the plugin module:

    bwg_auth = mrsm.Plugin('bwg-auth').module
    user = bwg_auth.bwg_user(request)      # {'username','is_admin','banned'} | None
    user = bwg_auth.require_user(request)  # raises HTTPException 401 / 403
    bwg_auth.moderation_check(text, username=user['username'])  # 'ok' | 'held'
    bwg_auth.rate_limited('bucket', user['username'], 10)        # True when over
    ext = bwg_auth.validate_image(upload)  # '.jpg' | '.png' | '.webp', else 400
"""

import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import meerschaum as mrsm
from meerschaum.plugins import api_plugin
from meerschaum.utils.warnings import warn

__version__ = '0.1.0'

BWG_SCOPE = 'bwg'
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
# Meerschaum's `build_where` silently DROPS a WHERE clause containing '--' or
# ';' (it would read every sign-in row), so those never reach a query.
EMAIL_BAD_RE = re.compile(r'--|[;\'"`\s]')
CODE_TTL = timedelta(minutes=10)
CODE_MAX_ATTEMPTS = 5
FAILS_PER_EMAIL = (20, 24 * 3600)  # 20 wrong codes / day per email, across codes
CODES_PER_EMAIL = (3, 15 * 60)     # 3 codes / 15 min per email
CODES_PER_IP = (20, 60 * 60)       # 20 codes / hour per IP
TOKEN_TTL = timedelta(days=365)
DISPLAY_NAME_MAX = 40
SETTINGS_MAX_BYTES = 64 * 1024
NEUTRAL_MESSAGE = 'If that address can receive email, a sign-in code is on its way.'

# Append-only: one 'code' row per code sent, a 'fail' row per wrong guess, a
# 'used' row once it signs someone in, and a 'token' row (code_id = the token
# id) per API key minted here (same pattern as community_revisions, so nothing
# is ever updated in place). Private: never served.
LOGIN_CODES_PIPE = mrsm.Pipe(
    'app', 'login_codes', 'BwgAuth', instance='sql:bwg',
    parameters={
        'autotime': True, 'schema': 'BwgAuth', 'target': 'login_codes',
        'columns': {'datetime': 'ts', 'id': 'id'},
        'dtypes': {'id': 'string', 'ts': 'datetime', 'kind': 'string',
                   'email': 'string', 'code_id': 'string', 'code_hash': 'string',
                   'expires': 'datetime', 'ip': 'string'},
    },
)
_CODES_LOCK = threading.Lock()


# ------------------------------------------------------------- moderation

# Whole-word matches (after leetspeak folding). Kept short on purpose: this
# holds text for a human to look at, it does not reject it.
_PROFANE_WORDS = frozenset('''
    cunt cunts bitch bitches bastard bastards dickhead dickheads cock cocks
    pussy pussies whore whores slut sluts twat twats wanker wankers asshole
    assholes fag fags faggot faggots retard retards retarded kike kikes spic
    spics chink chinks wetback wetbacks tranny trannies dyke dykes coon coons
    beaner beaners gook gooks cum jizz porn
'''.split())
# Substring roots that are never innocent inside another word.
_PROFANE_ROOTS = ('fuck', 'shit', 'nigger', 'nigga')
_LEET = str.maketrans('013457@$!', 'oieastasi')
_URL_RE = re.compile(r'https?://|www\.|\b[\w-]+\.(?:com|net|org|io|ru|xyz|info|biz|top|click|link)\b', re.I)
_REPEATS: dict[str, list[float]] = {}
_REPEATS_LOCK = threading.Lock()
REPEAT_MAX = 3
REPEAT_WINDOW_S = 10 * 60


def has_profanity(text: str | None) -> bool:
    """True when `text` contains a word from the (small) profanity list."""
    folded = (text or '').lower().translate(_LEET)
    if any(root in folded for root in _PROFANE_ROOTS):
        return True
    return any(w in _PROFANE_WORDS for w in re.findall(r'[a-z]+', folded))


def moderation_check(text: str | None, username: str | None = None) -> str:
    """'held' when public text needs a moderator's eyes, else 'ok'.

    Held: profanity, two or more links, shouting (>50% capitals on more than
    20 characters), or the same user submitting the same text more than 3
    times in 10 min (skipped when `username` is None, e.g. group-ride names).
    ponytail: the repeat counter is in-process (resets on restart, per
    worker); move it to the DB if the API ever runs several workers.
    """
    text = (text or '').strip()
    if not text:
        return 'ok'
    held = has_profanity(text) or len(_URL_RE.findall(text)) >= 2
    letters = [c for c in text if c.isalpha()]
    if len(text) > 20 and letters and sum(c.isupper() for c in letters) / len(letters) > 0.5:
        held = True
    if username is None:
        return 'held' if held else 'ok'
    key = f"{username}\n{' '.join(text.lower().split())}"
    now = time.time()
    with _REPEATS_LOCK:
        hits = [t for t in _REPEATS.get(key, []) if now - t < REPEAT_WINDOW_S] + [now]
        _REPEATS[key] = hits
        if len(_REPEATS) > 5000:
            for k in [k for k, v in _REPEATS.items() if now - v[-1] >= REPEAT_WINDOW_S]:
                _REPEATS.pop(k, None)
    return 'held' if held or len(hits) > REPEAT_MAX else 'ok'


# ------------------------------------------------------------- rate limits / uploads

# ponytail: in-process (per worker, resets on restart) like the other limiters.
_RATE_HITS: dict[tuple[str, str], list[float]] = {}
_RATE_LOCK = threading.Lock()


def rate_limited(bucket: str, key: str | None, limit: int, window_s: float = 3600) -> bool:
    """True when `key` used up `limit` hits in `bucket` this window (else counts one)."""
    now = time.time()
    with _RATE_LOCK:
        hits = [t for t in _RATE_HITS.get((bucket, key or 'unknown'), []) if now - t < window_s]
        limited = len(hits) >= limit
        if not limited:
            hits.append(now)
        _RATE_HITS[(bucket, key or 'unknown')] = hits
    return limited


def validate_image(upload) -> str:
    """'.jpg' / '.png' / '.webp' from the upload's magic bytes (never its
    name or content type), else HTTPException 400. Rewinds the file."""
    from fastapi import HTTPException
    head = upload.file.read(12)
    upload.file.seek(0)
    if head[:3] == b'\xff\xd8\xff':
        return '.jpg'
    if head[:4] == b'\x89PNG':
        return '.png'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return '.webp'
    raise HTTPException(status_code=400, detail='Photos must be JPEG, PNG or WebP.')


# ------------------------------------------------------------- users/tokens

def _users_conn():
    """The API's instance connector: where Meerschaum users + tokens live (the
    same instance `/login` and the Dash session use)."""
    from meerschaum.api import get_api_connector
    return get_api_connector()


def _user(username: str, conn=None):
    from meerschaum.core import User
    conn = conn or _users_conn()
    return User(username, instance=conn)


def _attributes(username: str, conn=None) -> dict:
    conn = conn or _users_conn()
    attrs = conn.get_user_attributes(_user(username, conn)) or {}
    return attrs if isinstance(attrs, dict) else {}


def _save_attributes(username: str, attrs: dict, conn=None) -> mrsm.SuccessTuple:
    """Rewrite only the attributes blob (edit_user skips empty fields)."""
    from meerschaum.core import User
    conn = conn or _users_conn()
    user = User(username, '', instance=conn)
    user._password_hash = ''
    user.type = ''
    user.email = ''
    user._attributes = attrs
    return conn.edit_user(user)


def _ensure_user(email: str) -> None:
    """Register the Meerschaum user on first sign-in."""
    from meerschaum.core import User
    conn = _users_conn()
    if conn.get_user_id(_user(email, conn)) is not None:
        return
    user = User(email, secrets.token_urlsafe(24), type='user',
                attributes={'scopes': [BWG_SCOPE], 'bwg': {}}, instance=conn)
    user.email = email
    success, msg = conn.register_user(user)
    if not success:
        raise RuntimeError(msg)


def _mint_token(username: str) -> str:
    """A 1-year Meerschaum API key scoped `bwg`, returned once."""
    from meerschaum.core import Token
    conn = _users_conn()
    now = datetime.now(timezone.utc)
    # The tokens table has a unique index on `label`: suffix it.
    token = Token(
        user=_user(username, conn),
        label=f'bwg-app {now:%Y-%m-%d} {secrets.token_hex(4)}',
        expiration=now + TOKEN_TTL,
        scopes=[BWG_SCOPE],
        instance=conn,
    )
    success, msg = token.register()
    if not success:
        raise RuntimeError(msg)
    # Provenance: `bwg_user` only honours (non-admin) tokens recorded here, so
    # a `bwg`-scoped key made through Meerschaum's own /tokens/register is refused.
    success, msg = LOGIN_CODES_PIPE.sync([{
        'id': secrets.token_hex(16), 'kind': 'token', 'email': username,
        'code_id': _token_key(token.id), 'code_hash': None, 'expires': None, 'ip': None,
    }])
    if not success:
        conn.delete_token(token)
        raise RuntimeError(msg)
    return token.get_api_key()


def _token_key(token_id) -> str:
    import uuid
    return str(uuid.UUID(str(token_id)))


def _minted_token(token_id) -> bool:
    """True when bwg-auth minted this token (a 'token' row in login_codes)."""
    if not LOGIN_CODES_PIPE.exists():
        return False
    df = LOGIN_CODES_PIPE.get_data(params={'kind': 'token', 'code_id': _token_key(token_id)},
                                   select_columns=['id'])
    return df is not None and len(df) > 0


def _resolve_token(api_key: str) -> tuple[str, str] | None:
    """(username, token_id) for a valid `bwg`-scoped API key, else None."""
    from fastapi import HTTPException
    from meerschaum.api._tokens import get_token_from_authorization
    try:
        token = get_token_from_authorization(api_key)
    except HTTPException:
        return None
    scopes = token.scopes or []
    if BWG_SCOPE not in scopes and '*' not in scopes:
        return None
    user_id = getattr(token, '_user_id', None)
    username = _username(user_id) if user_id is not None else None
    return (username, str(token.id)) if username else None


def _username(user_id) -> str | None:
    """Username for a users-table id, read the way `get_user_type` reads it.

    Not `token.user` / `conn.get_username`: on a SQL instance (4.1.1) those
    go through the users *pipe*, which resolved to a different instance than
    the users table in the local smoke test (id 19 -> None / wrong user).
    """
    conn = _users_conn()
    if getattr(conn, 'type', None) != 'sql':
        return conn.get_username(user_id)
    import sqlalchemy
    from meerschaum.connectors.sql.tables import get_tables
    users = get_tables(mrsm_instance=conn)['users']
    return conn.value(sqlalchemy.select(users.c.username).where(users.c.user_id == int(user_id)))


def _revoke_token(token_id: str) -> None:
    import uuid
    from meerschaum.core import Token
    conn = _users_conn()
    conn.delete_token(Token(id=uuid.UUID(token_id), instance=conn))


# Bearer -> user, briefly cached: each lookup is a DB read plus a 100k-round
# PBKDF2 check. ponytail: 60 s means a ban / admin change / revocation from
# ANOTHER process lands within a minute; sign-out here evicts immediately.
_USER_CACHE: dict[str, tuple[float, dict | None]] = {}
_USER_CACHE_TTL_S = 60


def _bearer(request) -> str | None:
    header = request.headers.get('authorization') or ''
    scheme, _, value = header.partition(' ')
    value = value.strip()
    if scheme.lower() != 'bearer' or not value.startswith('mrsm-key:'):
        return None
    return value


def bwg_user(request) -> dict | None:
    """The signed-in app user for this request, or None.

    Returns `{'username', 'is_admin', 'banned', 'token_id'}`.
    """
    api_key = _bearer(request)
    if not api_key:
        return None
    key = hashlib.sha256(api_key.encode()).hexdigest()
    hit = _USER_CACHE.get(key)
    if hit and time.time() - hit[0] < _USER_CACHE_TTL_S:
        return hit[1]
    resolved = _resolve_token(api_key)
    user = None
    if resolved:
        username, token_id = resolved
        conn = _users_conn()
        is_admin = conn.get_user_type(_user(username, conn)) == 'admin'
        # Admins may use a hand-minted key; everyone else only ours.
        if is_admin or _minted_token(token_id):
            attrs = _attributes(username, conn)
            bwg = attrs.get('bwg') if isinstance(attrs.get('bwg'), dict) else {}
            user = {
                'username': username,
                'is_admin': is_admin,
                'banned': bool(bwg.get('banned')),
                'token_id': token_id,
            }
    if len(_USER_CACHE) > 10000:
        _USER_CACHE.clear()
    _USER_CACHE[key] = (time.time(), user)
    return user


def require_user(request, write: bool = True) -> dict:
    """`bwg_user`, or HTTPException 401 (signed out) / 403 (banned, on writes)."""
    from fastapi import HTTPException
    user = bwg_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail='Sign in to do that.')
    if write and user['banned']:
        raise HTTPException(status_code=403, detail='This account can no longer post.')
    return user


# ------------------------------------------------------------- email codes

def _cfg(*keys, default=None):
    """`plugins:walk-audit:*` — the sign-in mail reuses walk-audit's SMTP."""
    try:
        value = mrsm.get_config('plugins', 'walk-audit', *keys, warn=False, write_missing=False)
    except Exception:
        return default
    return default if value is None or value == '' else value


def _send_code_email(email: str, code: str) -> bool:
    import smtplib
    from email.message import EmailMessage
    host, user, password = _cfg('smtp', 'host'), _cfg('smtp', 'username'), _cfg('smtp', 'password')
    if not (host and user and password):
        warn('bwg-auth: plugins:walk-audit:smtp unset; sign-in code not emailed.')
        return False
    msg = EmailMessage()
    msg['Subject'] = 'Your Bike Walk Greenville sign-in code'
    msg['From'] = user
    msg['To'] = email
    msg.set_content('\n'.join([
        f'Your Bike Walk Greenville sign-in code is {code}.',
        '',
        'It expires in 10 minutes. If you did not ask to sign in, ignore this email.',
        '',
        '-- Bike Walk Greenville',
    ]))
    try:
        with smtplib.SMTP(host, int(_cfg('smtp', 'port', default=587)), timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except Exception as e:
        warn(f'bwg-auth: SMTP send failed: {e}')
        return False
    return True


def _code_hash(email: str, code: str) -> str:
    # Salted with the email so one table leak can't be matched across users.
    return hashlib.sha256(f'{email}:{code}'.encode()).hexdigest()


def _recent_rows(params: dict, seconds: float) -> list[dict]:
    if not LOGIN_CODES_PIPE.exists():
        return []
    df = LOGIN_CODES_PIPE.get_data(
        params=params, begin=datetime.now(timezone.utc) - timedelta(seconds=seconds),
    )
    if df is None:
        raise RuntimeError('Sign-in codes could not be read.')
    return df.astype(object).where(df.notna(), None).to_dict(orient='records')


def _as_utc(ts):
    import pandas as pd
    ts = pd.Timestamp(ts)
    return ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')


def _valid_email(raw) -> str | None:
    from meerschaum.connectors.sql._users import valid_username
    email = str(raw or '').strip().lower()
    if not EMAIL_RE.match(email) or EMAIL_BAD_RE.search(email) or not valid_username(email)[0]:
        return None
    return email


def _profile(username: str) -> dict:
    conn = _users_conn()
    attrs = _attributes(username, conn)
    bwg = attrs.get('bwg') if isinstance(attrs.get('bwg'), dict) else {}
    return {
        'email': username,
        'display_name': bwg.get('display_name'),
        'is_admin': conn.get_user_type(_user(username, conn)) == 'admin',
        'disclaimer_version': bwg.get('disclaimer_version'),
        'disclaimer_accepted_at': bwg.get('disclaimer_accepted_at'),
        'settings': bwg.get('settings') or {},
    }


@api_plugin
def init_app(app):
    import json
    import uuid
    from fastapi import Request, HTTPException
    from fastapi.responses import JSONResponse

    def _err(message: str, status: int):
        return JSONResponse({'error': message}, status_code=status)

    async def _json(request: Request) -> dict:
        if len(await request.body()) > SETTINGS_MAX_BYTES + 4096:
            raise ValueError('Request too large.')
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        return body

    @app.post('/bwg/auth/request-code')
    async def request_code(request: Request):
        try:
            body = await _json(request)
        except Exception:
            return _err('Expected JSON with an email.', 400)
        email = _valid_email(body.get('email'))
        if email is None and EMAIL_BAD_RE.search(str(body.get('email') or '').strip()):
            return _err('Email addresses cannot contain spaces, quotes, semicolons or "--".', 400)
        if email is None:
            # valid_username also rejects '+' and addresses over 60 characters.
            return _err('Enter a valid email address (letters, numbers, . _ - only; up to 60 characters).', 400)
        ip = request.client.host if request.client else 'unknown'
        with _CODES_LOCK:
            try:
                by_email = [r for r in _recent_rows({'email': email, 'kind': 'code'}, CODES_PER_EMAIL[1])]
                by_ip = [r for r in _recent_rows({'ip': ip, 'kind': 'code'}, CODES_PER_IP[1])]
            except RuntimeError:
                return _err('Sign-in is unavailable right now. Please retry.', 503)
            if len(by_email) >= CODES_PER_EMAIL[0] or len(by_ip) >= CODES_PER_IP[0]:
                return _err('Too many codes requested. Please wait a few minutes and try again.', 429)
            code = f'{secrets.randbelow(10 ** 6):06d}'
            code_id = uuid.uuid4().hex
            success, _ = LOGIN_CODES_PIPE.sync([{
                'id': code_id, 'kind': 'code', 'email': email, 'code_id': code_id,
                'code_hash': _code_hash(email, code),
                'expires': datetime.now(timezone.utc) + CODE_TTL, 'ip': ip,
            }])
        if not success:
            return _err('Sign-in is unavailable right now. Please retry.', 503)
        threading.Thread(target=_send_code_email, args=(email, code), daemon=True).start()
        return {'ok': True, 'message': NEUTRAL_MESSAGE}

    @app.post('/bwg/auth/verify')
    async def verify_code(request: Request):
        try:
            body = await _json(request)
        except Exception:
            return _err('Expected JSON with email and code.', 400)
        email = _valid_email(body.get('email'))
        code = str(body.get('code') or '').strip()
        if email is None or not re.fullmatch(r'\d{6}', code):
            return _err('That code is not valid. Request a new one.', 401)
        with _CODES_LOCK:
            try:
                rows = _recent_rows({'email': email}, CODE_TTL.total_seconds() + 60)
            except RuntimeError:
                return _err('Sign-in is unavailable right now. Please retry.', 503)
            try:
                fails_today = _recent_rows({'email': email, 'kind': 'fail'}, FAILS_PER_EMAIL[1])
            except RuntimeError:
                return _err('Sign-in is unavailable right now. Please retry.', 503)
            if len(fails_today) >= FAILS_PER_EMAIL[0]:
                return _err('Too many wrong codes for this address today. Please try again tomorrow.', 429)
            now = datetime.now(timezone.utc)
            codes = sorted((r for r in rows if r.get('kind') == 'code'), key=lambda r: _as_utc(r['ts']))
            latest = codes[-1] if codes else None
            if latest is None or _as_utc(latest['expires']) <= now:
                return _err('That code expired. Request a new one.', 401)
            used = any(r.get('kind') == 'used' and r.get('code_id') == latest['id'] for r in rows)
            fails = sum(r.get('kind') == 'fail' and r.get('code_id') == latest['id'] for r in rows)
            if used or fails >= CODE_MAX_ATTEMPTS:
                return _err('That code is no longer valid. Request a new one.', 401)
            ok = hmac.compare_digest(latest['code_hash'] or '', _code_hash(email, code))
            LOGIN_CODES_PIPE.sync([{
                'id': uuid.uuid4().hex, 'kind': 'used' if ok else 'fail', 'email': email,
                'code_id': latest['id'], 'code_hash': None, 'expires': None,
                'ip': request.client.host if request.client else None,
            }])
        if not ok:
            return _err('That code is not right. Check the email and try again.', 401)
        try:
            _ensure_user(email)
            token = _mint_token(email)
            profile = _profile(email)
        except Exception as e:
            warn(f'bwg-auth: sign-in failed for a verified code: {e}')
            return _err('Could not finish signing in. Please retry.', 503)
        return {'token': token, 'email': email, 'display_name': profile['display_name'],
                'is_admin': profile['is_admin']}

    @app.get('/bwg/auth/me')
    def get_me(request: Request):
        try:
            user = require_user(request, write=False)
        except HTTPException as e:
            return _err(e.detail, e.status_code)
        return _profile(user['username'])

    @app.put('/bwg/auth/me')
    async def put_me(request: Request):
        try:
            user = require_user(request)
        except HTTPException as e:
            return _err(e.detail, e.status_code)
        try:
            body = await _json(request)
        except Exception:
            return _err('Expected a JSON object.', 400)
        conn = _users_conn()
        attrs = _attributes(user['username'], conn)
        bwg = dict(attrs.get('bwg') or {}) if isinstance(attrs.get('bwg'), dict) else {}
        if 'display_name' in body:
            name = ' '.join(str(body['display_name'] or '').split())
            if len(name) > DISPLAY_NAME_MAX:
                return _err(f'Display names are up to {DISPLAY_NAME_MAX} characters.', 400)
            if has_profanity(name):
                return _err('Please choose a different display name.', 400)
            bwg['display_name'] = name or None
        if 'settings' in body:
            if not isinstance(body['settings'], dict):
                return _err('Settings must be a JSON object.', 400)
            if len(json.dumps(body['settings'])) > SETTINGS_MAX_BYTES:
                return _err('Settings are too large.', 413)
            bwg['settings'] = body['settings']
        if body.get('disclaimer_version') is not None:
            version = body['disclaimer_version']
            if isinstance(version, bool) or not isinstance(version, int) or not 0 < version < 1000:
                return _err('disclaimer_version must be a positive integer.', 400)
            if version != bwg.get('disclaimer_version'):
                bwg['disclaimer_version'] = version
                bwg['disclaimer_accepted_at'] = datetime.now(timezone.utc).isoformat()
        attrs = dict(attrs)
        attrs['bwg'] = bwg
        attrs.setdefault('scopes', [BWG_SCOPE])
        success, msg = _save_attributes(user['username'], attrs, conn)
        if not success:
            warn(f'bwg-auth: could not save profile: {msg}')
            return _err('Could not save your profile. Please retry.', 503)
        return _profile(user['username'])

    @app.delete('/bwg/auth/token')
    def delete_token(request: Request):
        user = bwg_user(request)
        if user is None:
            return _err('Sign in to do that.', 401)
        try:
            _revoke_token(user['token_id'])
        except Exception as e:
            warn(f'bwg-auth: could not revoke token: {e}')
            return _err('Could not sign out. Please retry.', 503)
        _USER_CACHE.pop(hashlib.sha256(_bearer(request).encode()).hexdigest(), None)
        return {'ok': True}
