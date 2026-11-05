from types import SimpleNamespace

from scripts import framework_qwen_remote


def test_remote_launcher_keeps_key_out_of_command_and_sends_it_once_on_stdin(tmp_path, monkeypatch, capsys):
    key = 'synthetic-only-' + 'x' * 40
    env = tmp_path / 'app.env'
    env.write_text('A02_QWEN_API_KEY=' + key + '\n', encoding='utf-8')
    received = {}

    def fake_run(command, **kwargs):
        received['command'] = command
        received['input'] = kwargs['input']
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(framework_qwen_remote.subprocess, 'run', fake_run)
    assert framework_qwen_remote.main(['--host', '127.0.0.1', '--port', '20719', '--env-file', str(env)]) == 0
    assert received['input'] == (key + '\n').encode()
    assert key not in ' '.join(received['command'])
    assert key not in capsys.readouterr().out
    assert received['command'][-1] == framework_qwen_remote.REMOTE_COMMAND


def test_remote_launcher_rejects_missing_key(tmp_path, monkeypatch):
    env = tmp_path / 'app.env'
    env.write_text('A02_QWEN_API_KEY=short\n', encoding='utf-8')
    monkeypatch.setattr(framework_qwen_remote.subprocess, 'run',
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not connect')))
    assert framework_qwen_remote.main(['--host', '127.0.0.1', '--port', '20719', '--env-file', str(env)]) == 1
