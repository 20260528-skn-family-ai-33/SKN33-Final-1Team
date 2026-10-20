"""Read-only M008 schema/privilege preflight; never migrates, seeds or starts API."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from backend.app.config import ConfigurationError,load_settings,read_environment_file
from backend.app.persistence.database import PersistenceError,create_app_engine
from backend.app.persistence.schema_inventory import check_schema


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,help='Explicit literal app environment file; process values take precedence.')
    args=parser.parse_args(argv);engine=None
    try:
        settings=load_settings(read_environment_file(args.env_file) if args.env_file else None)
        engine=create_app_engine(settings)
        print(json.dumps(check_schema(engine,settings)));return 0
    except (ConfigurationError,PersistenceError) as error:
        print(json.dumps({'status':'error','code':error.code,'message':str(error)}));return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
