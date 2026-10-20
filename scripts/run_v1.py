"""Explicit v1 launcher. Never reads the Compose .env, migrates, seeds or installs."""
import argparse
import os
from pathlib import Path
from ipaddress import ip_address
from backend.app.config import ConfigurationError, read_environment_file
from backend.app.v1_config import load_v1_settings


def launch_options(settings, values, host, port):
    try:
        if not 1 <= port <= 65535: raise ValueError()
        address = ip_address(host)
        if not settings.secure and not address.is_loopback: raise ValueError()
    except ValueError: raise ConfigurationError('Use an explicit IP, valid port and loopback for development.') from None
    trusted = values.get('TRUSTED_PROXY_IPS', '')
    if settings.secure:
        try:
            if not trusted or any(not value.strip() for value in trusted.split(',')): raise ValueError()
            for value in trusted.split(','): ip_address(value.strip())
        except ValueError: raise ConfigurationError('Production requires exact TRUSTED_PROXY_IPS; wildcard and networks are not allowed.') from None
    elif trusted: raise ConfigurationError('Development must not trust forwarded headers.')
    return dict(host=host, port=port, workers=1, proxy_headers=settings.secure,
                forwarded_allow_ips=trusted if settings.secure else '', access_log=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run the explicit PostgreSQL v1 API and optional React build.')
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        values = read_environment_file(args.env_file)
        settings = load_v1_settings(values)
        options = launch_options(settings, values, args.host, args.port)
        if settings.frontend_dist:
            from backend.app.web import prepare_build
            prepare_build(settings.frontend_dist)
        if args.check_only:
            print('v1 configuration/build valid; DB, storage and OCR startup not executed.')
            return 0
        os.environ['CC_APP_ENV_FILE'] = str(args.env_file.resolve())
        import uvicorn
        uvicorn.run('backend.app.main:create_app', factory=True, **options)
        return 0
    except ConfigurationError:
        print('v1 configuration rejected. Check explicit app-only environment, build and proxy settings.')
        return 1


if __name__ == '__main__': raise SystemExit(main())
