import json
import os
from pathlib import Path

import pytest

from scripts.prepare_a02_presentation import prepare
from scripts.framework_desktop import read_env

ROOT=Path(__file__).resolve().parents[1]


def source(tmp_path,extra=''):
    values={'APP_ENV':'test','PERSISTENCE_MODE':'postgres','DB_HOST':'localhost','DB_PORT':'5432',
        'POSTGRES_DB':'creative_checker','DB_APP_USER':'creative_checker_app','DB_APP_PASSWORD':'test-private-password',
        'AUTH_ALLOWED_ORIGINS':'http://localhost:8012','AUTH_ALLOWED_HOSTS':'localhost',
        'CSRF_SECRET':'c'*40,'CURSOR_SECRET':'r'*40,'A02_QWEN_API_KEY':'test-model-key'}
    path=tmp_path/'original.env';path.write_text(''.join(k+'='+v+'\n' for k,v in values.items())+extra)
    config=tmp_path/'original.json';config.write_bytes((ROOT/'deploy/qwen-a02/worker-config.example.json').read_bytes())
    return path,config


def test_prepare_copies_app_settings_preserves_secrets_roles_and_originals_without_overwrite(tmp_path):
    env,config=source(tmp_path);before=(env.read_bytes(),config.read_bytes())
    output_env=tmp_path/'prepared.env';output_config=tmp_path/'prepared.json'
    result=prepare(env,config,output_env,output_config)
    values=read_env(output_env);team=json.loads(output_config.read_text())
    assert values['A02_QWEN_API_KEY']=='test-model-key' and values['DB_APP_PASSWORD']=='test-private-password'
    assert 'http://127.0.0.1:5173' in values['AUTH_ALLOWED_ORIGINS'] and '127.0.0.1' in values['AUTH_ALLOWED_HOSTS']
    original_roles=json.loads(config.read_text())['roles']
    assert {role:{key:team['roles'][role][key] for key in original} for role,original in original_roles.items()}==original_roles
    assert team['pdf_extraction']['presentation'] is True and team['pdf_extraction']['timeout_seconds']==600
    assert (env.read_bytes(),config.read_bytes())==before
    assert 'test-model-key' not in json.dumps(result) and 'test-private-password' not in json.dumps(result)
    if os.name=='posix':assert output_env.stat().st_mode&0o777==0o600
    prepared=(output_env.read_bytes(),output_config.read_bytes())
    with pytest.raises(ValueError):prepare(env,config,output_env,output_config)
    assert prepared==(output_env.read_bytes(),output_config.read_bytes())


def test_prepare_refuses_owner_credentials_before_creating_files(tmp_path):
    env,config=source(tmp_path,'DB_OWNER_PASSWORD=owner-secret\n')
    output_env=tmp_path/'prepared.env';output_config=tmp_path/'prepared.json'
    with pytest.raises(ValueError,match='app-only'):prepare(env,config,output_env,output_config)
    assert not output_env.exists() and not output_config.exists()
