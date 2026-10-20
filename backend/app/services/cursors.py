"""Opaque, context bound, expiring keyset cursors. Rights are rechecked by services."""
import base64
import hashlib
import hmac
import json
import time
from ..hashing.canonical import canonical
from ..security.errors import AuthError


def encode(raw): return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')
def decode(value): return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


class Cursors:
    def __init__(self, secret, clock=time.time):
        self.key = secret.reveal().encode()
        self.clock = clock

    def issue(self, context, last):
        raw = canonical(dict(context=context, last=last, expires=int(self.clock()) + 86400))
        return encode(raw) + '.' + encode(hmac.digest(self.key, raw, 'sha256'))

    def read(self, token, context):
        try:
            if not isinstance(token, str) or len(token) > 2048: raise ValueError()
            body, signature = token.split('.')
            raw = decode(body)
            if not hmac.compare_digest(decode(signature), hmac.digest(self.key, raw, 'sha256')): raise ValueError()
            value = json.loads(raw)
            if value['context'] != context or value['expires'] <= self.clock(): raise ValueError()
            if not isinstance(value['last'], list) or len(value['last']) != 2: raise ValueError()
            return value['last']
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise AuthError('CURSOR_INVALID', 400) from None
