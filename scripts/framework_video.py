"""Start the separate loopback-only video API with the existing app session."""
import argparse
from pathlib import Path
import sys

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
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Invalid port.')

    from backend.app.config import ConfigurationError
    from backend.runtime_environment import activate_runtime
    from backend.app.v1_config import load_v1_settings
    from backend.app.video.framework_service import create_video_app
    import portalocker
    import uvicorn

    try:
        settings = load_v1_settings(activate_runtime('legacy', args.env_file))
        root = ROOT / '.runtime' / 'framework-video-local'
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with portalocker.Lock(str(root / '.service.lock'), mode='a', timeout=0,
                              flags=portalocker.LOCK_EX | portalocker.LOCK_NB):
            app = create_video_app(settings, root)
            uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1,
                        proxy_headers=False, reload=False)
    except (ConfigurationError, portalocker.exceptions.LockException) as error:
        print(f'VIDEO_SERVICE_NOT_READY: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
