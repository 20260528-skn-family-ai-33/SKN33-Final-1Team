"""Shared Django settings. This prototype is served on localhost only."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = Path(os.environ.get('LATTE_RUNTIME_DIR', BASE_DIR / 'data/runtime')).resolve()
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def local_secret():
    # Persist the key so that restarting the local server preserves admin sessions.
    key_file = RUNTIME_DIR / '.django-secret-key'
    if key_file.exists():
        return key_file.read_text().strip()
    candidate = RUNTIME_DIR / ('.django-secret-key-' + secrets.token_hex(8))
    fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(secrets.token_urlsafe(64))
        try:
            os.link(candidate, key_file)
        except FileExistsError:
            pass
    finally:
        candidate.unlink(missing_ok=True)
    return key_file.read_text().strip()


SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY') or local_secret()
DEBUG = False
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '[::1]']
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'inspections',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'config.urls'
ASGI_APPLICATION = 'config.asgi.application'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(RUNTIME_DIR / 'latte.sqlite3'),
    'OPTIONS': {'timeout': 20}, 'CONN_MAX_AGE': 0,
}}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'ko-kr'
TIME_ZONE = 'Asia/Seoul'
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'web']
STATIC_ROOT = BASE_DIR / '.runtime/static'
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
