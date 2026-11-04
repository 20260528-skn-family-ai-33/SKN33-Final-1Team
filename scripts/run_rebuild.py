"""Single local entry point for the rebuilt service; no installations or migrations."""
import argparse
from ipaddress import ip_address
from pathlib import Path

from backend.app.config import ConfigurationError
from backend.runtime_environment import activate_runtime


def main(argv=None):
    parser = argparse.ArgumentParser(description="Creative Checker API / worker / MCP")
    parser.add_argument("component", choices=["api", "worker", "projection", "mcp"], nargs="?", default="api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--env-file", type=Path, help="Explicit runtime file; otherwise use process variables only.")
    args = parser.parse_args(argv)
    try:
        activate_runtime(args.component, args.env_file)
    except ConfigurationError as error:
        parser.error(str(error))
    from backend.creative_checker.settings import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.cookie_secure and not ip_address(args.host).is_loopback:
        parser.error("HTTP development must bind to loopback. Use secure cookies behind HTTPS in deployment.")
    if args.component in {"worker", "projection"}:
        from backend.creative_checker.worker import run
        run(projection_only=args.component == "projection")
    else:
        import uvicorn
        module = "api" if args.component == "api" else "tools"
        uvicorn.run(f"backend.creative_checker.{module}:create_app", factory=True,
                    host=args.host, port=args.port or (8000 if module == "api" else 8129),
                    proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
