"""Mounted only by the C15 test override. Never copied into release images."""
import argparse,signal,threading
from pathlib import Path
from backend.app.worker import load_worker_settings,run_worker
from backend.app.config import read_environment_file,ConfigurationError
from backend.tests.c15_helpers import RehearsalPipeline


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--env-file',type=Path,required=True);args=parser.parse_args()
    settings=load_worker_settings(read_environment_file(args.env_file))
    if settings.environment!='test':raise ConfigurationError('Synthetic rehearsal worker requires APP_ENV=test.')
    stop=threading.Event()
    for signum in (signal.SIGTERM,signal.SIGINT):signal.signal(signum,lambda *_:stop.set())
    return run_worker(settings,stop,adapter=RehearsalPipeline())


if __name__=='__main__':raise SystemExit(main())
