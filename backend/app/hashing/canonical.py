"""RFC 8785 bytes, without Unicode normalization or an added newline."""
import hashlib
import json
import math
import rfc8785
from ..security.errors import AuthError

MAX_BYTES = 262144


def validate_tree(value):
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 32 or nodes > 100000:
            raise ValueError('JSON complexity limit')
        if isinstance(item, str):
            if '\x00' in item or any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise ValueError('Unsupported string')
        elif isinstance(item, dict):
            pending.extend((v, depth + 1) for pair in item.items() for v in pair)
        elif isinstance(item, list):
            pending.extend((v, depth + 1) for v in item)
        elif isinstance(item, bool) or item is None:
            pass
        elif isinstance(item, int):
            if abs(item) > 2**53 - 1:
                raise ValueError('Unsafe JSON integer')
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError('Nonfinite number')
        else:
            raise ValueError('Unsupported JSON value')


def canonical(value):
    validate_tree(value)
    return rfc8785.dumps(value)


def digest(kind, payload, contract='cc-content-jcs-v1'):
    return hashlib.sha256(canonical({'contract': contract, 'kind': kind, 'payload': payload})).hexdigest()


def loads(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Duplicate JSON member')
            result[key] = value
        return result
    if len(raw) > MAX_BYTES:
        raise AuthError('PAYLOAD_TOO_LARGE', 413)
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        validate_tree(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise AuthError('INVALID_JSON', 400) from None
