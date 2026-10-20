"""HTTP credential parsing and cookie projection; database work stays in one worker call."""
import json,secrets
from fastapi import APIRouter,Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse,Response
from ...contracts.auth import Credentials
from ...security.errors import AuthError
from ...security.csrf import developer_allowed,session_token


def envelope(request,data):
    return {**data,'meta':{'contract_version':'cc-api-v1','request_id':request.state.request_id}}


def set_cookie(response,settings,name,value,*,httponly,max_age):
    response.set_cookie(name,value,max_age=max_age,httponly=httponly,secure=settings.secure,samesite='strict',path='/')


def delete_cookie(response,settings,name,*,httponly):
    response.delete_cookie(name,path='/',secure=settings.secure,httponly=httponly,samesite='strict')


def reject_duplicates(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError()
        result[key]=value
    return result


async def parse_credentials(request):
    if request.headers.get('content-type','').split(';',1)[0].strip().lower()!='application/json':raise AuthError('UNSUPPORTED_MEDIA_TYPE',415)
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>4096:raise AuthError('PAYLOAD_TOO_LARGE',413)
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=reject_duplicates,parse_constant=lambda x:(_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError,RecursionError):raise AuthError('INVALID_JSON',400) from None
    try:
        if not isinstance(value,dict):raise ValueError()
        credentials=Credentials.model_validate(value)
        if any('\x00' in v or any(0xD800<=ord(c)<=0xDFFF for c in v) for v in value.values() if isinstance(v,str)):raise ValueError()
        return credentials
    except (ValueError,ValidationError):raise AuthError('INVALID_AUTH_REQUEST',422) from None


def router():
    result=APIRouter(prefix='/api/v1/auth')

    @result.get('/csrf')
    def csrf(request:Request):
        settings=request.app.state.settings;signer=request.app.state.csrf
        purpose,binding=signer.binding(request,settings)
        fresh_seed=False
        if not binding:binding=secrets.token_urlsafe(32);fresh_seed=True
        token=request.cookies.get(settings.csrf_cookie)
        if not signer.valid(token,purpose,binding):token=signer.issue(purpose,binding)
        response=JSONResponse(envelope(request,{'csrf_token':token,'developer_login_available':developer_allowed(request,settings)}))
        # Do not extend a reused token's absolute signature expiry, even if its browser cookie is refreshed.
        set_cookie(response,settings,settings.csrf_cookie,token,httponly=False,max_age=900)
        if fresh_seed:set_cookie(response,settings,settings.seed_cookie,binding,httponly=True,max_age=900)
        return response

    @result.post('/login')
    async def login(request:Request):
        settings=request.app.state.settings
        credentials=await parse_credentials(request)
        user,raw=await run_in_threadpool(request.app.state.auth.login,credentials,request.cookies.get(settings.session_cookie),
            ip=request.client.host if request.client else 'unknown',developer_allowed=developer_allowed(request,settings),request_id=request.state.request_id)
        response=JSONResponse(envelope(request,{'user':user}))
        set_cookie(response,settings,settings.session_cookie,raw,httponly=True,max_age=28800)
        set_cookie(response,settings,settings.csrf_cookie,request.app.state.csrf.issue('session',raw),httponly=False,max_age=900)
        delete_cookie(response,settings,settings.seed_cookie,httponly=True)
        delete_cookie(response,settings,'baro_local_session',httponly=True)
        return response

    @result.get('/me')
    def me(request:Request):
        settings=request.app.state.settings
        subject=request.app.state.auth.current(request.cookies.get(settings.session_cookie),developer_allowed=developer_allowed(request,settings))
        return envelope(request,{'authenticated':subject is not None,'user':subject.public() if subject else None})

    @result.post('/logout')
    def logout(request:Request):
        settings=request.app.state.settings
        request.app.state.auth.logout(request.cookies.get(settings.session_cookie),request_id=request.state.request_id)
        response=Response(status_code=204)
        for name,private in ((settings.session_cookie,True),(settings.csrf_cookie,False),(settings.seed_cookie,True),('baro_local_session',True)):
            delete_cookie(response,settings,name,httponly=private)
        return response

    return result
