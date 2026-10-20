"""Real Argon2id, bounded concurrency; construct once at explicit app startup."""
from contextlib import contextmanager
from threading import BoundedSemaphore
import secrets
from .errors import AuthError


def normalize_email(value):
    from email_validator import validate_email,EmailNotValidError
    if not isinstance(value,str):raise AuthError('INVALID_AUTH_REQUEST',422)
    value=value.strip().lower()
    if not 1<=len(value)<=254 or not value.isascii():raise AuthError('INVALID_AUTH_REQUEST',422)
    try:return validate_email(value,allow_smtputf8=False,check_deliverability=False).normalized.lower()
    except EmailNotValidError:raise AuthError('INVALID_AUTH_REQUEST',422) from None


def validate_password(value,*,issuing=False):
    if not isinstance(value,str) or '\x00' in value or not (15 if issuing else 1)<=len(value)<=128:
        raise AuthError('INVALID_AUTH_REQUEST',422)
    try:value.encode('utf-8')
    except UnicodeEncodeError:raise AuthError('INVALID_AUTH_REQUEST',422) from None
    return value


class Passwords:
    def __init__(self):
        from argon2 import PasswordHasher,Type
        self.hasher=PasswordHasher(time_cost=2,memory_cost=19456,parallelism=1,hash_len=32,salt_len=16,type=Type.ID)
        self.slots=BoundedSemaphore(2)
        self.dummy=self.hasher.hash(secrets.token_urlsafe(32))

    @contextmanager
    def slot(self):
        if not self.slots.acquire(blocking=False):raise AuthError('AUTH_RATE_LIMITED',429,retry_after=1)
        try:yield
        finally:self.slots.release()

    def hash(self,password):
        validate_password(password,issuing=True)
        with self.slot():return self.hasher.hash(password)

    def verify(self,password,encoded):
        from argon2 import extract_parameters,Type
        from argon2.exceptions import VerificationError,InvalidHashError
        with self.slot():
            try:valid=self.hasher.verify(encoded or self.dummy,password)
            except (VerificationError,InvalidHashError):return False,None
            if not encoded or not valid:return False,None
            parameters=extract_parameters(encoded)
            # Never lower any existing memory/time/parallelism/hash/salt cost.
            costs=(parameters.memory_cost,parameters.time_cost,parameters.parallelism,parameters.hash_len,parameters.salt_len)
            target=(19456,2,1,32,16)
            upgrade=all(a<=b for a,b in zip(costs,target)) and (any(a<b for a,b in zip(costs,target)) or parameters.type!=Type.ID)
            return True,self.hasher.hash(password) if upgrade else None
