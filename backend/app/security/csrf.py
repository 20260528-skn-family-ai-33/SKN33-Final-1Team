"""Signed double-submit bound to the raw session token or a private pre-auth seed."""
import hashlib,hmac,re,secrets,time
from urllib.parse import urlsplit
from .errors import AuthError
from ..v1_config import origin,is_loopback

TOKEN=re.compile(r'^[A-Za-z0-9_-]{43}$')


def session_token(value):return value if isinstance(value,str) and TOKEN.fullmatch(value) else None


def source_origin(request):
    value=request.headers.get('origin')
    if value is not None:return origin(value) if value!='null' else None
    value=request.headers.get('referer')
    if not value:return None
    try:
        parsed=urlsplit(value)
        return origin(parsed.scheme+'://'+parsed.netloc)
    except ValueError:return None


def developer_allowed(request,settings):
    return bool(settings.developer_login and settings.environment in ('development','test')
        and request.client and is_loopback(request.client.host) and request.url.hostname in ('localhost','127.0.0.1','::1')
        and not any(k.lower()=='forwarded' or k.lower().startswith('x-forwarded-') for k in request.headers)
        and source_origin(request) in settings.origins)


class Csrf:
    def __init__(self,secret,clock=time.time):self.key=secret.reveal().encode();self.clock=clock

    def _signature(self,purpose,binding,nonce,issued):
        pieces=[v.encode('utf-8') for v in ('cc-csrf-v1',purpose,binding,nonce,issued)]
        message=b''.join(len(v).to_bytes(4,'big')+v for v in pieces)
        import base64
        return base64.urlsafe_b64encode(hmac.new(self.key,message,hashlib.sha256).digest()).rstrip(b'=').decode()

    def issue(self,purpose,binding):
        issued=str(int(self.clock()));nonce=secrets.token_urlsafe(32)
        return issued+'.'+nonce+'.'+self._signature(purpose,binding,nonce,issued)

    def valid(self,token,purpose,binding):
        if not token or len(token)>256 or not binding:return False
        try:
            issued,nonce,signature=token.split('.')
            if not re.fullmatch(r'[0-9]{1,12}',issued) or not TOKEN.fullmatch(nonce) or not TOKEN.fullmatch(signature):return False
            age=self.clock()-int(issued)
            return -30<=age<900 and hmac.compare_digest(signature,self._signature(purpose,binding,nonce,issued))
        except (ValueError,UnicodeError):return False

    def binding(self,request,settings):
        token=session_token(request.cookies.get(settings.session_cookie))
        if token:return 'session',token
        return 'pre-auth',session_token(request.cookies.get(settings.seed_cookie))

    def verify(self,request,settings):
        token=request.cookies.get(settings.csrf_cookie,'');header=request.headers.get('x-csrftoken','')
        purpose,binding=self.binding(request,settings)
        if (source_origin(request) not in settings.origins or not token or len(header)>256
            or not hmac.compare_digest(token.encode('utf-8'),header.encode('utf-8')) or not self.valid(token,purpose,binding)):
            raise AuthError('CSRF_FAILED',403)
