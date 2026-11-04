import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.framework_proxy import framework_proxy_router


def test_same_origin_proxy_preserves_auth_csrf_request_key_and_multiple_cookies():
    calls=[]
    def upstream(request):
        calls.append(request)
        assert request.url.host=='127.0.0.1' and request.url.port==8012
        assert request.headers['origin']=='http://localhost:8000'
        assert request.headers['x-csrftoken']=='upstream-token'
        assert request.headers['cookie']=='baro_session=test-session'
        assert request.headers['idempotency-key']=='request-key'
        assert request.content==b'%PDF-data'
        return httpx.Response(201,json={'document':'stored'},headers=[('set-cookie','csrftoken=fresh; Path=/'),('set-cookie','baro_csrf_seed=seed; Path=/')])
    app=FastAPI();app.include_router(framework_proxy_router(transport=httpx.MockTransport(upstream)))
    with TestClient(app) as client:
        response=client.post('/api/framework/v1/projects/p/documents?filename=brief.pdf',content=b'%PDF-data',
            headers={'Origin':'http://localhost:8000','X-CSRFToken':'upstream-token','Cookie':'baro_session=test-session',
                'Idempotency-Key':'request-key','Content-Type':'application/pdf'})
    assert response.status_code==201 and response.json()=={'document':'stored'}
    assert len(response.headers.get_list('set-cookie'))==2
    assert len(calls)==1 and calls[0].url.params['filename']=='brief.pdf'


@pytest.mark.parametrize('status',[401,403,409,503])
def test_proxy_preserves_upstream_rejections_instead_of_falling_back(status):
    app=FastAPI();app.include_router(framework_proxy_router(transport=httpx.MockTransport(
        lambda _:httpx.Response(status,json={'error':{'code':'source_error'}}))))
    with TestClient(app) as client:response=client.get('/api/framework/v1/projects')
    assert response.status_code==status and response.json()['error']['code']=='source_error'


def test_proxy_transport_error_is_a_document_service_error():
    def broken(request):raise httpx.ConnectError('offline',request=request)
    app=FastAPI();app.include_router(framework_proxy_router(transport=httpx.MockTransport(broken)))
    with TestClient(app) as client:response=client.get('/api/framework/v1/health')
    assert response.status_code==503 and response.json()['error']['code']=='DOCUMENT_SERVICE_UNAVAILABLE'


@pytest.mark.parametrize('origin',['http://example.com:8012','http://user:secret@localhost:8012','http://localhost:8012/api'])
def test_proxy_target_is_server_configuration_and_cannot_be_a_browser_url(origin):
    with pytest.raises(ValueError):framework_proxy_router(origin=origin)
