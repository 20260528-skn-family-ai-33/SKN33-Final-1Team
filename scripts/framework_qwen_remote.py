"""Start the existing loopback-only RunPod Qwen server over SSH.

The app's key is read from an explicit local env file and passed over SSH stdin,
never in argv, the remote shell command, or a new file. This foreground process
keeps the SSH session open. Existing servers and tunnels are never stopped.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import ConfigurationError, read_environment_file

REMOTE_COMMAND = (
    'IFS= read -r VLLM_API_KEY || exit 1; export VLLM_API_KEY; '
    'exec /opt/creative-checker-a02/runtime/venv/bin/python '
    '/opt/creative-checker-a02/repository/deploy/qwen-a02/server.py '
    'serve --config /opt/creative-checker-a02/repository/deploy/qwen-a02/server-poster.json'
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--user', default='root')
    parser.add_argument('--identity', type=Path, default=Path.home() / '.ssh/id_ed25519')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.runtime/framework-desktop-full-review.env')
    args = parser.parse_args(argv)
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', args.host)
            or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', args.user)
            or not 1 <= args.port <= 65535):
        parser.error('Invalid SSH host, user or port.')
    try:
        key = read_environment_file(args.env_file, {}).get('A02_QWEN_API_KEY', '')
        if len(key) < 32 or any(char in key for char in '\x00\r\n'):
            raise ConfigurationError('A02_QWEN_API_KEY is not configured.')
        command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                   '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
                   '-i', str(args.identity.expanduser()), '-p', str(args.port),
                   args.user + '@' + args.host, REMOTE_COMMAND]
        print('Qwen 연결 중: 모델 검증에 시간이 걸릴 수 있습니다. 이 터미널을 켜 두세요.', flush=True)
        return subprocess.run(command, input=(key + '\n').encode('utf-8'), check=False).returncode
    except (ConfigurationError, OSError) as error:
        print('QWEN_START_FAILED:', type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
