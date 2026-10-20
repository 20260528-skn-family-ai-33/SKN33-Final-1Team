"""Explicit retirement boundary; never imports or starts the legacy application."""
from ipaddress import ip_address
from starlette.responses import JSONResponse


def review_path(path):
    return path.rstrip('/') == '/api/reviews' or path.startswith('/api/reviews/')


def retired_response(request_id):
    return JSONResponse({'error': {'code': 'LEGACY_WRITE_DISABLED',
        'message': '새 검수는 v1 화면에서 이미지 등록·기준 확정 후 시작해 주세요.', 'fields': {}},
        'meta': {'contract_version': 'cc-api-v1', 'request_id': request_id}},
        status_code=410, headers={'Cache-Control': 'no-store', 'X-Request-ID': request_id})


def local_read_request(request):
    try:
        return bool(request.client and ip_address(request.client.host).is_loopback
            and request.url.hostname in ('localhost', '127.0.0.1', '::1'))
    except ValueError:
        return False
