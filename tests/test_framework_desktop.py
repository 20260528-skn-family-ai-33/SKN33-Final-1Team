"""Cross-platform argv, credential isolation and container storage boundaries."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('framework_desktop',ROOT/'scripts/framework_desktop.py')
desktop=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(desktop)


def args(action,*extra):return desktop.parser().parse_args([action,*extra])


@pytest.mark.parametrize('endpoint',('http://localhost:18015/v1/chat/completions','http://127.0.0.1:18015/v1/chat/completions','http://[::1]:18015/v1/chat/completions'))
def test_loopback_is_mapped_to_the_pc_running_the_tunnel(endpoint):
    assert desktop.mapped_endpoint(endpoint)=='http://host.docker.internal:18015/v1/chat/completions'


def test_external_trusted_endpoint_is_preserved():
    value='https://example.test/v1/chat/completions'
    assert desktop.mapped_endpoint(value)==value


def test_windows_bom_crlf_settings_are_literal_and_not_shell_code(tmp_path):
    path=tmp_path/'Windows 설정.env'
    path.write_bytes(b'\xef\xbb\xbfDB_HOST=localhost\r\nDB_APP_PASSWORD=literal$(&value)=kept\r\n')
    assert desktop.read_env(path)=={'DB_HOST':'localhost','DB_APP_PASSWORD':'literal$(&value)=kept'}


def test_private_derived_settings_do_not_modify_original_or_leak_values(tmp_path,capsys):
    env=tmp_path/'팀 공통 & 환경.env';original='DB_HOST=127.0.0.1\nDB_APP_PASSWORD=test-private-value\n'
    env.write_text(original)
    config=ROOT/'deploy/qwen-a02/worker-config.example.json';original_config=config.read_bytes()
    a=args('api','--env-file',str(env),'--config',str(config))
    with desktop.settings(a) as folder:
        assert 'DB_HOST=host.docker.internal' in (folder/'app.env').read_text()
        assert 'test-private-value' in (folder/'app.env').read_text()
        assert json.loads((folder/'team.json').read_text())['pdf_extraction']['endpoint'].startswith('http://host.docker.internal:18015/')
        command=desktop.docker_command(a,folder)
        assert '127.0.0.1:8012:8012' in command
        assert 'cc-framework-data:/data' in command
        assert command[command.index('--host')+1]=='0.0.0.0'
        assert not any('test-private-value' in part for part in command)
    assert not folder.exists() and env.read_text()==original and config.read_bytes()==original_config
    assert not capsys.readouterr().out


def test_owner_credentials_cannot_be_sent_to_api(tmp_path):
    env=tmp_path/'owner.env';env.write_text('DB_OWNER_PASSWORD=private-test-only\n')
    with pytest.raises(ValueError,match='APP_CREDENTIALS_ONLY'):
        with desktop.settings(args('api','--env-file',str(env))):pytest.fail('must not create API settings')


def test_migration_is_explicit_and_has_no_server_or_storage_initialization(tmp_path):
    env=tmp_path/'owner.env';env.write_text('DB_HOST=localhost\nDB_OWNER_PASSWORD=private-test-only\n')
    a=args('migrate','--env-file',str(env),'--revision','AF003')
    with desktop.settings(a) as folder:cmd=desktop.docker_command(a,folder)
    assert 'migrate-postgres' in cmd and 'AF003' in cmd and 'private-test-only' not in cmd
    assert '--publish' not in cmd and 'cc-framework-data:/data' not in cmd


def test_existing_poster_profile_requires_an_explicit_existing_linux_volume(tmp_path):
    env=tmp_path/'app.env';env.write_text('DB_HOST=example.test\n')
    a=args('worker','--env-file',str(env),'--config',str(ROOT/'examples/agent_framework/team_config.poster-review.template.json'))
    with pytest.raises(ValueError,match='EXISTING_POSTER_VOLUME_REQUIRED'):
        with desktop.settings(a):pytest.fail('a Windows folder must not silently replace a Linux store')


def test_shared_pdf_volume_and_read_only_inputs_are_literal_arguments(tmp_path):
    folder=tmp_path/'한글 & team space';folder.mkdir()
    command=desktop.docker_command(args('worker','--once','--volume','team-review-data'),folder)
    assert 'team-review-data:/data' in command and '--once' in command
    assert str((folder/'app.env').resolve())+':/run/framework/app.env:ro' in command
    assert str((folder/'team.json').resolve())+':/run/framework/team.json:ro' in command
    assert '--publish' not in command


def test_poster_directory_cannot_escape_volume(tmp_path):
    a=args('worker','--poster-volume','images','--poster-subdir','../outside')
    with pytest.raises(ValueError,match='POSTER_SUBDIR_INVALID'):desktop.docker_command(a,tmp_path)


def test_windows_identity_and_spaces_are_passed_without_shell_interpolation(monkeypatch):
    seen=[]
    def run(command,**kwargs):seen.append((command,kwargs));return SimpleNamespace(returncode=0)
    monkeypatch.setattr(desktop.subprocess,'run',run)
    identity=r'C:\Users\팀원 & 개발\.ssh\id_ed25519'
    assert desktop.main(['tunnel','--ssh-host','213.173.99.23','--identity',identity])==0
    cmd,kwargs=seen[0]
    assert cmd[cmd.index('-i')+1]==identity and kwargs['shell'] is False
    assert '127.0.0.1:18015:127.0.0.1:8015' in cmd


def test_doctor_refuses_windows_container_engine(monkeypatch,capsys):
    monkeypatch.setattr(desktop.subprocess,'run',lambda *_args,**_kwargs:SimpleNamespace(returncode=0,stdout='windows\n'))
    assert desktop.main(['doctor'])==2
    assert 'DOCKER_LINUX_ENGINE_REQUIRED' in capsys.readouterr().err


def test_default_native_api_stays_loopback_and_container_bind_is_explicit():
    from backend.agent_framework.cli import parser
    p=parser();base=['postgres-serve','--env-file','app.env','--config','team.json']
    assert p.parse_args(base).host=='127.0.0.1'
    assert p.parse_args(base+['--host','0.0.0.0']).host=='0.0.0.0'


def test_init_config_never_overwrites_existing_credentials(tmp_path):
    target=tmp_path/'팀원 설정.env'
    assert desktop.main(['init-config','--env-file',str(target)])==0
    original=target.read_bytes()
    assert desktop.main(['init-config','--env-file',str(target)])==2
    assert target.read_bytes()==original
