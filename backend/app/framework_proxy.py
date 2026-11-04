"""Same-origin bridge for the existing front-v2 and the authenticated A02 API."""
import os
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter,Request
from starlette.responses import JSONResponse,Response

MAX_BYTES=21*1024*1024


def framework_proxy_router(*,origin=None,transport=None):
    target=origin or os.getenv('FRAMEWORK_API_ORIGIN','http://127.0.0.1:8012')
    url=urlsplit(target)
    if (url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost','::1','host.docker.internal')
            or url.username or url.password or url.path not in ('','/') or url.query or url.fragment):
        raise ValueError('FRAMEWORK_API_ORIGIN must be a trusted local HTTP origin')
    url.port
    target=target.rstrip('/')
    router=APIRouter()

    def unavailable():
        return JSONResponse({'error':{'code':'DOCUMENT_SERVICE_UNAVAILABLE',
            'message':'PDF 분석 서버에 연결하지 못했습니다. API·worker·연결 설정을 확인해 주세요.'}},status_code=503)

    @router.api_route('/api/framework/{path:path}',methods=['GET','POST','PATCH'])
    async def forward(request:Request,path:str):
        if not path.startswith('v1/') or any(part in ('.','..') for part in path.split('/')):
            return Response(status_code=404)
        raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>MAX_BYTES:
                return JSONResponse({'error':{'code':'PAYLOAD_TOO_LARGE','message':'PDF 요청 크기를 확인해 주세요.'}},status_code=413)
            raw.extend(chunk)
        allowed=('cookie','origin','referer','x-csrftoken','idempotency-key','content-type','accept')
        headers={name:request.headers[name] for name in allowed if name in request.headers}
        # Origin and CSRF are preserved; the upstream M008 boundary authorizes
        # every operation. A model URL/key can never be selected by the browser.
        try:
            async with httpx.AsyncClient(transport=transport,trust_env=False,follow_redirects=False,
                    timeout=httpx.Timeout(30,connect=5)) as client:
                response=await client.request(request.method,target+'/api/framework/'+path,
                    params=request.query_params.multi_items(),headers=headers,content=bytes(raw))
        except httpx.TransportError:return unavailable()
        if 300<=response.status_code<400:return unavailable()
        result=Response(content=response.content,status_code=response.status_code,
            headers={'Content-Type':response.headers.get('content-type','application/json'),'Cache-Control':'no-store'})
        for cookie in response.headers.get_list('set-cookie'):
            result.raw_headers.append((b'set-cookie',cookie.encode('latin-1')))
        return result

    return router
