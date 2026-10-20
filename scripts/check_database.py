"""Read-only C01 connection check. Does not start the API, migrate or seed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.config import ConfigurationError, load_settings, read_environment_file
from backend.app.persistence.database import PersistenceError, check_connection, create_app_engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Explicit literal environment file; exported values take precedence.")
    args = parser.parse_args(argv)
    engine = None
    try:
        settings = load_settings(read_environment_file(args.env_file) if args.env_file else None)
        if settings.persistence_mode == "legacy_json":
            print(json.dumps({"status": "disabled", "persistence_mode": "legacy_json", "database_checked": False}))
            return 0
        engine = create_app_engine(settings)
        print(json.dumps(check_connection(engine, settings)))
        return 0
    except (ConfigurationError, PersistenceError) as error:
        result = {"status": "error", "code": error.code, "message": str(error)}
        if isinstance(error, PersistenceError):
            result.update(outcome=error.outcome, sqlstate=error.sqlstate, retryable=error.retryable)
        print(json.dumps(result))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
