"""Explicit synthetic rehearsal data, never a deployed runtime selector."""
from pathlib import Path
from backend.app.execution.pipeline import code_revision
from backend.tests.c10_fixtures import SyntheticPipeline


class RehearsalPipeline(SyntheticPipeline):
    def describe(self):
        return dict(pipeline='paddle_full_v1',model_name='SYNTHETIC_REHEARSAL_NOT_A_MODEL',
            model_revision={'synthetic_rehearsal':True},device_policy=['cpu'],execution_target='local',
            code_revision=code_revision(),preprocessing={'version':'cc-display-2x-lanczos-v1','ocr_scale':2})
    def recognize(self,*args):
        result=super().recognize(*args);result['pipeline']='paddle_full_v1';return result


def production_values(db,root,config,**extra):
    from backend.tests.auth_fixtures import auth_environment
    return auth_environment(db,APP_ENV='production',LOCAL_ADMIN_LOGIN='0',DEV_PROJECT_ID='',
        AUTH_ALLOWED_ORIGINS='https://localhost:8443',AUTH_ALLOWED_HOSTS='localhost',TRUSTED_PROXY_IPS='172.30.215.10',
        STORAGE_ROOT=str(root),FRONTEND_DIST=str(Path('frontend/dist-v1').resolve()),
        REVIEW_EXECUTOR='postgres_worker',REVIEW_PIPELINE='disabled',REVIEW_CONFIG_FILE=str(config),**extra)


def worker_values(web):
    names=('PERSISTENCE_MODE','APP_ENV','LOCAL_ADMIN_LOGIN','DB_HOST','DB_PORT','POSTGRES_DB','DB_APP_USER','DB_APP_PASSWORD','STORAGE_ROOT','REVIEW_EXECUTOR')
    return dict({key:web[key] for key in names},REVIEW_PIPELINE='paddle-only',RUN_TARGET='local')
