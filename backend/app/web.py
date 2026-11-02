"""Opt-in, validated v1 build serving. API and missing files never receive SPA HTML."""
import json
import re
from pathlib import Path
from starlette.responses import FileResponse
from .config import ConfigurationError
from .security.errors import AuthError

UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
PAGE = re.compile(rf'^/(?:|login|modeltest/?|projects|review/new|projects/{UUID}/(?:review/new|drafts|reviews|(?:drafts|criteria|reviews)/{UUID}))$')
ASSET = re.compile(r'^[A-Za-z0-9_-]+-[A-Za-z0-9_-]{8,}\.(?:js|css|woff2|png|svg)$')
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
MODELTEST_CSP = CSP.replace("connect-src 'self'", "connect-src 'self' https://*.proxy.runpod.net http://localhost:* https://localhost:* http://127.0.0.1:* https://127.0.0.1:*").replace("object-src", "media-src 'self' blob:; object-src")


def prepare_build(root: Path):
    try:
        if root.is_symlink() or not root.is_dir(): raise ValueError()
        marker = json.loads((root / 'cc-frontend.json').read_text())
        if marker != {'contract': 'cc-web-v1', 'profile': 'v1'}: raise ValueError()
        index = root / 'index.html'
        if index.is_symlink() or not index.is_file() or index.stat().st_size > 1024 * 1024: raise ValueError()
        source = index.read_text()
        if '<div id="root"' not in source or not re.search(r'/assets/[^"<>]+\.js', source): raise ValueError()
        assets = root / 'assets'
        if assets.is_symlink() or not assets.is_dir(): raise ValueError()
        for item in assets.iterdir():
            if item.is_symlink() or not item.is_file() or not ASSET.fullmatch(item.name): raise ValueError()
        for name in re.findall(r'(?:src|href)="/assets/([^"<>]+)"', source):
            if not ASSET.fullmatch(name) or not (assets / name).is_file(): raise ValueError()
    except (OSError, ValueError, TypeError):
        raise ConfigurationError('FRONTEND_DIST requires a complete v1 build with cc-web-v1 marker.') from None
    return root


def install_routes(app, root):
    @app.api_route('/{path:path}', methods=['GET', 'HEAD'])
    def page(path: str):
        if PAGE.fullmatch('/' + path): return FileResponse(root / 'index.html', media_type='text/html')
        parts = path.split('/')
        if len(parts) == 2 and parts[0] == 'assets' and ASSET.fullmatch(parts[1]):
            asset = root / 'assets' / parts[1]
            if asset.is_file() and not asset.is_symlink(): return FileResponse(asset)
        raise AuthError('RESOURCE_NOT_FOUND', 404)


def headers(response, *, secure, modeltest=False):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy'] = MODELTEST_CSP if modeltest else CSP
    if secure: response.headers['Strict-Transport-Security'] = 'max-age=31536000'
