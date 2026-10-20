"""Test-only child-process factory; no real model, network API or deployment use."""
import os
from pathlib import Path
import time
from backend.app.config import read_environment_file,ConfigurationError
from backend.app.v1_config import load_v1_settings
from backend.app.main import create_app
from backend.tests.c10_fixtures import SyntheticPipeline


def factory():
    settings=load_v1_settings(read_environment_file(Path(os.environ['CC_APP_ENV_FILE'])))
    if settings.environment!='test':raise ConfigurationError('Synthetic factory requires APP_ENV=test')
    root=Path(os.environ['CC_C10_TEST_GATE_ROOT'])
    class Gate(SyntheticPipeline):
        def recognize(self,*args):
            with (root/'calls').open('a') as stream:stream.write('called\n')
            deadline=time.monotonic()+20
            while not (root/'allow').exists():
                if time.monotonic()>deadline:raise RuntimeError('synthetic test gate expired')
                time.sleep(.02)
            return super().recognize(*args)
    return create_app(settings=settings,review_adapter=Gate())
