"""Start the loopback-only video API with the existing app session."""
import argparse
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Optional isolated FFmpeg wheel; the existing project virtualenv is untouched.
EXTRA_DEPENDENCIES = ROOT / '.runtime' / 'framework-video-deps'
if EXTRA_DEPENDENCIES.is_dir():
    sys.path.insert(1, str(EXTRA_DEPENDENCIES))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8013)
    parser.add_argument('--aws-test', action='store_true')
    parser.add_argument('--storage-root', type=Path)
    parser.add_argument('--worker-token-file', type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Invalid port.')
    if args.aws_test:
        if not args.storage_root or not args.storage_root.is_absolute() or not args.worker_token_file:
            parser.error('AWS test requires an absolute storage root and a worker token file.')
    elif args.storage_root or args.worker_token_file:
        parser.error('AWS storage and token options require --aws-test.')

    from backend.app.config import ConfigurationError
    from backend.runtime_environment import activate_runtime
    from backend.app.v1_config import load_v1_settings
    from backend.app.video.framework_service import create_video_app
    import portalocker
    import uvicorn

    try:
        settings = load_v1_settings(activate_runtime('legacy', args.env_file))
        if args.aws_test:
            from backend.agent_framework.deployment import require_verified_database
            require_verified_database(settings)
            worker_url = urlsplit(os.environ.get('VIDEO_LOGO_WORKER_URL', ''))
            if worker_url.scheme != 'http' or worker_url.hostname != '127.0.0.1' or not worker_url.port:
                raise ConfigurationError('AWS test requires a loopback video worker tunnel.')
            token = args.worker_token_file.read_text().strip()
            if len(token) < 32 or not token.isascii() or not all(c.isalnum() or c in '-_' for c in token):
                raise ConfigurationError('Invalid video worker token file.')
            os.environ['VIDEO_LOGO_WORKER_TOKEN'] = token
        root = args.storage_root if args.aws_test else ROOT / '.runtime' / 'framework-video-local'
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with portalocker.Lock(str(root / '.service.lock'), mode='a', timeout=0,
                              flags=portalocker.LOCK_EX | portalocker.LOCK_NB):
            app = create_video_app(settings, root, aws_test=args.aws_test)
            uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1,
                        proxy_headers=args.aws_test, forwarded_allow_ips='127.0.0.1', reload=False)
    except (ConfigurationError, OSError, ValueError, portalocker.exceptions.LockException) as error:
        print(f'VIDEO_SERVICE_NOT_READY: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
