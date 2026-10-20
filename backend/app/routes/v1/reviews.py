"""Review request/response projection and bounded JSON/download streams."""
import json
from tempfile import SpooledTemporaryFile
from fastapi import APIRouter,Request,BackgroundTasks
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse,StreamingResponse
from starlette.background import BackgroundTask
from ...contracts.reviews import ReviewCreate,Retry
from ...security.errors import AuthError
from .business import identifier,subject,parse
from .auth import envelope

MAX_RESPONSE=2*1024*1024
MAX_EXPORT=50*1024*1024


def bounded(request,data):
    response=JSONResponse(envelope(request,data))
    if len(response.body)>MAX_RESPONSE:
        return JSONResponse(dict(error=dict(code='RESPONSE_TOO_LARGE',message='결과 JSON 다운로드를 이용해 주세요.',fields={
            'export_url':'/api/v1/reviews/'+data['id']+'/export'} if 'id' in data else {}),meta=envelope(request,{})['meta']),status_code=503)
    return response


def query(request,allowed):
    values=request.query_params
    if any(key not in allowed|{'limit','cursor'} or len(values.getlist(key))!=1 for key in values):raise AuthError('INVALID_REQUEST',422)
    limit=values.get('limit','20')
    if not limit.isascii() or not limit.isdecimal() or not 1<=int(limit)<=100:raise AuthError('INVALID_REQUEST',422)
    return values,int(limit),values.get('cursor')


async def parse_review(request,model):
    if request.headers.get('Content-Encoding','identity').lower()!='identity':raise AuthError('UNSUPPORTED_MEDIA_TYPE',415)
    return await parse(request,model)


def router():
    result=APIRouter(prefix='/api/v1')

    async def admit(request,tasks,body,key,source=None):
        who,dev=await run_in_threadpool(subject,request)
        service=request.app.state.reviews
        data,replayed=await run_in_threadpool(service.admit,who,dev,body,key,request.state.request_id,source)
        if data['status']=='queued' and request.app.state.review_executor is not None:
            tasks.add_task(request.app.state.review_executor.execute,identifier(data['id']))
        return JSONResponse(envelope(request,data),status_code=200 if replayed else 202,headers={
            'Location':'/api/v1/reviews/'+data['id'],**({'Idempotency-Replayed':'true'} if replayed else {})})

    def key(request):
        keys=request.headers.getlist('Idempotency-Key')
        if len(keys)!=1:raise AuthError('INVALID_IDEMPOTENCY_KEY',422)
        value=identifier(keys[0])
        if value.version!=4:raise AuthError('INVALID_IDEMPOTENCY_KEY',422)
        return value

    @result.post('/reviews')
    async def create(request:Request,background_tasks:BackgroundTasks):
        body=await parse_review(request,ReviewCreate)
        return await admit(request,background_tasks,body.model_dump(),key(request))

    @result.post('/reviews/{run_id}/retry')
    async def retry(request:Request,run_id:str,background_tasks:BackgroundTasks):
        body=await parse_review(request,Retry)
        return await admit(request,background_tasks,body.model_dump(),key(request),identifier(run_id))

    @result.get('/projects/{project_id}/reviews')
    def listing(request:Request,project_id:str):
        values,limit,cursor=query(request,{'status','image_id'})
        filters={}
        if 'status' in values:
            if values['status'] not in ('queued','running','completed','failed','imported'):raise AuthError('INVALID_REQUEST',422)
            filters['status']=values['status']
        if 'image_id' in values:filters['image_id']=str(identifier(values['image_id']))
        return bounded(request,request.app.state.review_queries.listing(*subject(request),identifier(project_id),filters,limit,cursor))

    @result.get('/reviews/{run_id}')
    def detail(request:Request,run_id:str):
        return bounded(request,request.app.state.review_queries.detail(*subject(request),identifier(run_id)))

    @result.get('/reviews/{run_id}/ocr-records')
    def ocr(request:Request,run_id:str):
        values,limit,cursor=query(request,set())
        return bounded(request,request.app.state.review_queries.page(*subject(request),identifier(run_id),'ocr',limit,cursor))

    @result.get('/reviews/{run_id}/results/{result_id}/evidence')
    def evidence(request:Request,run_id:str,result_id:str):
        values,limit,cursor=query(request,set())
        return bounded(request,request.app.state.review_queries.page(*subject(request),identifier(run_id),'evidence',limit,cursor,identifier(result_id)))

    @result.get('/reviews/{run_id}/export')
    def export(request:Request,run_id:str):
        query(request,set())
        data=request.app.state.review_queries.export(*subject(request),identifier(run_id))
        stream=SpooledTemporaryFile(max_size=1024*1024,mode='w+b');size=0
        try:
            for part in json.JSONEncoder(ensure_ascii=False,allow_nan=False,separators=(',',':')).iterencode(data):
                raw=part.encode('utf-8');size+=len(raw)
                if size>MAX_EXPORT:raise AuthError('EXPORT_TOO_LARGE',422)
                stream.write(raw)
            stream.seek(0)
        except BaseException:stream.close();raise
        def chunks():
            try:
                while chunk:=stream.read(65536):yield chunk
            finally:stream.close()
        return StreamingResponse(chunks(),media_type='application/json',headers={'Content-Length':str(size),'X-Content-Type-Options':'nosniff',
            'Content-Disposition':'attachment; filename="review-'+str(identifier(run_id))+'.json"'},background=BackgroundTask(stream.close))

    return result
