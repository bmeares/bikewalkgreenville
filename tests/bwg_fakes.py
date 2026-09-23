"""Shared stubs for the bwg-auth tests: no Postgres, no SMTP, no token DB.

`Authorization: Bearer mrsm-key:<username>` resolves to <username> through the
REAL `bwg_user` logic; only the token lookup and the users table are faked.
"""
import importlib.util
import os
import sys

import pandas as pd

PLUGIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'plugins', 'bwg-auth.py')
spec = importlib.util.spec_from_file_location('bwg_auth', PLUGIN)
auth = importlib.util.module_from_spec(spec)
sys.modules['bwg_auth'] = auth
spec.loader.exec_module(auth)
#: The real provenance check (install() stubs it; test_bwg_auth exercises it).
real_minted_token = auth._minted_token

#: Smallest byte strings validate_image accepts.
JPEG = b'\xff\xd8\xff\xe0 fake jpeg'
PNG = b'\x89PNG\r\n\x1a\n fake'


def bearer(username):
    return {'Authorization': f'Bearer mrsm-key:{username}'}


class FakeUsers:
    """The slice of the Meerschaum instance connector bwg-auth touches."""

    def __init__(self, admins=(), banned=()):
        self.users = {}
        for name in admins:
            self.users[name] = {'type': 'admin', 'attributes': {'bwg': {}}}
        for name in banned:
            self.users[name] = {'type': 'user', 'attributes': {'bwg': {'banned': True}}}
        self.revoked = []

    def _get(self, user):
        return self.users.setdefault(user.username, {'type': 'user', 'attributes': {'scopes': ['bwg'], 'bwg': {}}})

    def get_user_id(self, user):
        return 1 if user.username in self.users else None

    def get_user_type(self, user):
        return self._get(user)['type']

    def get_user_attributes(self, user):
        return self._get(user)['attributes']

    def register_user(self, user):
        self.users[user.username] = {'type': user.type, 'attributes': dict(user.attributes), 'email': user.email}
        return True, 'ok'

    def edit_user(self, user):
        self._get(user)['attributes'] = user.attributes
        return True, 'ok'


def install(fake_users):
    """Point bwg-auth at `fake_users`; tokens are 'mrsm-key:<username>'."""
    auth._USER_CACHE.clear()
    auth._REPEATS.clear()
    auth._RATE_HITS.clear()
    auth._minted_token = lambda token_id: True
    auth._users_conn = lambda: fake_users
    auth._resolve_token = lambda key: (key.split(':', 1)[1], 'token-' + key.split(':', 1)[1])
    auth._revoke_token = fake_users.revoked.append
    return auth


class MemoryPipe:
    """Append-only pipe stub with the read paths the plugins use."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.fail = False
        self.instance_connector = self

    def exists(self):
        return bool(self.rows)

    def read(self, _sql):
        return pd.DataFrame(self.rows)

    def get_data(self, params=None, begin=None, **_):
        rows = [r for r in self.rows if all(r.get(k) == v for k, v in (params or {}).items())]
        if begin is not None:
            rows = [r for r in rows if pd.Timestamp(r['ts']) >= pd.Timestamp(begin)]
        return pd.DataFrame(rows)

    def clear(self, params=None, **_):
        self.rows = [r for r in self.rows if not all(r.get(k) == v for k, v in (params or {}).items())]
        return True, 'cleared'

    def sync(self, rows, **_):
        if self.fail:
            return False, 'unavailable'
        now = pd.Timestamp.now(tz='UTC')
        self.rows.extend(dict(r, ts=now, created=now) for r in rows)
        return True, 'saved'
