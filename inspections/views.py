from django.conf import settings
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET


@require_GET
@ensure_csrf_cookie
def index(request):
    versions = {name + '_version': (settings.BASE_DIR / 'web' / file).stat().st_mtime_ns
                for name, file in [('style', 'styles.css'), ('script', 'app.js')]}
    response = render(request, 'inspections/index.html', versions)
    response['Cache-Control'] = 'no-store'
    return response


@require_GET
@ensure_csrf_cookie
def csrf(request):
    response = JsonResponse({'csrf_token': get_token(request)})
    response['Cache-Control'] = 'no-store'
    return response
