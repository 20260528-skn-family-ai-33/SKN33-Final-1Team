"""Test-only synthetic process adapter. APP_ENV=test required; never a deployment entrypoint."""
import argparse,os,signal,threading,time
from pathlib import Path
from backend.app import worker
from backend.app.config import read_environment_file,ConfigurationError
from backend.app.execution.queue import WorkerQueue
from backend.tests.c10_fixtures import SyntheticPipeline


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--env-file',type=Path,required=True);parser.add_argument('--gate',type=Path,required=True)
    parser.add_argument('--before-dispatch',action='store_true');parser.add_argument('--once',action='store_true');args=parser.parse_args()
    settings=worker.load_worker_settings(read_environment_file(args.env_file))
    if settings.environment!='test':raise ConfigurationError('Synthetic worker requires test environment.')
    gate=args.gate
    def wait(name):
        deadline=time.monotonic()+15
        while not (gate/name).exists():
            if time.monotonic()>deadline:raise RuntimeError('Synthetic test gate expired')
            time.sleep(.02)
    class Queue(WorkerQueue):
        def __init__(self,*a,**kw):super().__init__(*a,**kw,lease_seconds=1)
        def claim_next(self):
            claimed=super().claim_next()
            if claimed and args.before_dispatch:(gate/'claimed').touch();wait('claim-allow')
            return claimed
    class Lease(worker.AttemptLease):
        def __init__(self,*a,**kw):super().__init__(*a,**kw,interval=.1)
    class Adapter(SyntheticPipeline):
        def recognize(self,*a):
            fd=os.open(gate/'calls',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            with os.fdopen(fd,'w') as stream:stream.write('called\n');stream.flush();os.fsync(stream.fileno())
            (gate/'entered').touch();wait('allow')
            return super().recognize(*a)
    worker.WorkerQueue=Queue;worker.AttemptLease=Lease
    stop=threading.Event()
    for signum in (signal.SIGTERM,signal.SIGINT):signal.signal(signum,lambda *_:stop.set())
    return worker.run_worker(settings,stop,once=args.once,adapter=Adapter())


if __name__=='__main__':raise SystemExit(main())
