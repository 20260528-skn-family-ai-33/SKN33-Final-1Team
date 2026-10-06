import os


def setup_django():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    from django.apps import apps
    if not apps.ready:
        django.setup()
