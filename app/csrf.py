"""Apply Django's cookie/header/origin CSRF checks to FastAPI mutations."""
import io

from django.core.handlers.asgi import ASGIRequest
from django.http import QueryDict
from django.middleware.csrf import CsrfViewMiddleware
from django.utils.datastructures import MultiValueDict
from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

csrf_header = APIKeyHeader(name='X-CSRFToken', auto_error=False, scheme_name='DjangoCSRF')


def enforce_csrf(request: Request, token: str | None = Security(csrf_header)):
    if request.method in {'GET', 'HEAD', 'OPTIONS', 'TRACE'}:
        return
    django_request = ASGIRequest(request.scope, io.BytesIO())
    # FastAPI owns JSON and multipart parsing. Check only the CSRF header here.
    django_request._post = QueryDict()
    django_request._files = MultiValueDict()
    checker = CsrfViewMiddleware(lambda req: None)
    checker.process_request(django_request)
    if checker.process_view(django_request, lambda req: None, (), {}) is not None:
        raise HTTPException(403, detail={
            'code': 'CSRF_FAILED',
            'message': '요청 인증값이 없거나 만료되었습니다. 화면을 새로고침한 뒤 다시 시도하세요.',
        })
