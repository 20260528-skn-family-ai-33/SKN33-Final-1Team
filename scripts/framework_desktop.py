"""Mac/Windows host launcher for a shared Linux framework runtime.

Host needs Python + Docker Desktop; native Windows POSIX-storage emulation is
not attempted. Real payloads stay in a Linux named volume, never an NTFS bind.
No DB bootstrap/migration, image build, storage init or SSH key copying is implicit.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path,PurePosixPath
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit,urlunsplit

ROOT=Path(__file__).resolve().parents[1]
IMAGE='creative-checker-framework:local'
VOLUME='cc-framework-data'


def name(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}',value):raise argparse.ArgumentTypeError('Use a Docker name, not a host filesystem path.')
    return value


def read_env(path):
    values={}
    for raw in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line=raw.strip()
        if not line or line.startswith('#'):continue
        key,sep,value=line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*',key) or key in values:raise ValueError('ENV_FORMAT_INVALID')
        values[key]=value
    return values


def mapped_endpoint(value):
    url=urlsplit(value)
    if url.hostname not in ('localhost','127.0.0.1','::1'):return value
    if url.username or url.password:raise ValueError('ENDPOINT_CREDENTIALS_FORBIDDEN')
    host='host.docker.internal'+(':'+str(url.port) if url.port else '')
    return urlunsplit((url.scheme,host,url.path,url.query,url.fragment))


def write_private(path,raw):
    with Path(path).open('x',encoding='utf-8',newline='\n') as stream:stream.write(raw)
    Path(path).chmod(0o600)


@contextmanager
def settings(args):
    values=read_env(args.env_file)
    if args.action!='migrate' and any(values.get(k) for k in ('DB_OWNER_PASSWORD','POSTGRES_PASSWORD')):
        raise ValueError('APP_CREDENTIALS_ONLY')
    if values.get('CC_NEO4J_URI'):
        values['CC_NEO4J_URI']=mapped_endpoint(values['CC_NEO4J_URI'])
    if values.get('DB_HOST') in ('localhost','127.0.0.1','::1'):values['DB_HOST']='host.docker.internal'
    config=None
    if args.action in ('api','worker'):
        config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
        if not isinstance(config,dict) or not isinstance(config.get('roles'),dict):raise ValueError('TEAM_CONFIG_INVALID')
        pdf=config.get('pdf_extraction')
        if pdf is not None and not isinstance(pdf,dict):raise ValueError('TEAM_CONFIG_INVALID')
        if pdf and pdf.get('mode')=='qwen':pdf['endpoint']=mapped_endpoint(pdf['endpoint'])
        inspector=config['roles'].get('A04_A05')
        if not isinstance(inspector,dict):raise ValueError('TEAM_CONFIG_INVALID')
        if inspector.get('kind')=='registered_poster' and not args.poster_volume:
            raise ValueError('EXISTING_POSTER_VOLUME_REQUIRED')
    with tempfile.TemporaryDirectory(prefix='cc-framework-desktop-') as directory:
        folder=Path(directory)
        write_private(folder/'app.env',''.join(k+'='+v+'\n' for k,v in values.items()))
        if config is not None:write_private(folder/'team.json',json.dumps(config,ensure_ascii=False,indent=2)+'\n')
        yield folder


def mount_file(source,target):
    # Separate argv tokens preserve Windows drive letters, spaces and Korean.
    return ['--volume',str(Path(source).resolve())+':'+target+':ro']


def docker_command(args,folder=None):
    if args.action=='build':
        return ['docker','build','--file',str(ROOT/'deploy/agent-framework/Dockerfile'),'--tag',args.image,str(ROOT)]
    base=['docker','run','--rm','--init']
    if sys.platform.startswith('linux'):base+=['--add-host','host.docker.internal:host-gateway']
    if args.container_name:base+=['--name',args.container_name]
    if args.network:base+=['--network',args.network]
    if args.action in ('init-pdf','api','worker'):base+=['--volume',args.volume+':/data']
    if args.action=='init-pdf':return base+[args.image,'init-pdf-storage','--root','/data/pdf']
    if args.action=='demo':return base+[args.image,'demo']
    base+=mount_file(folder/'app.env','/run/framework/app.env')
    if args.action in ('api','worker'):base+=mount_file(folder/'team.json','/run/framework/team.json')
    if args.action=='api':base+=['--publish',f'127.0.0.1:{args.port}:8012']
    if args.poster_volume:
        parts=PurePosixPath(args.poster_subdir)
        if parts.is_absolute() or '..' in parts.parts or '\\' in args.poster_subdir:raise ValueError('POSTER_SUBDIR_INVALID')
        base+=['--volume',args.poster_volume+':/poster']
    action={'api':'postgres-serve','check':'postgres-check','migrate':'migrate-postgres'}.get(args.action,args.action)
    base+=[args.image,action,'--env-file','/run/framework/app.env']
    if args.action=='migrate':base+=['--revision',args.revision]
    if args.action in ('api','worker'):
        base+=['--config','/run/framework/team.json','--pdf-storage-root','/data/pdf']
        if args.poster_volume:base+=['--poster-storage-root',str(PurePosixPath('/poster')/args.poster_subdir)]
    if args.action=='api':base+=['--host','0.0.0.0','--port','8012']
    if args.action in ('worker','graph-projector') and args.once:base+=['--once']
    if args.action=='graph-rebuild':base+=['--project-id',str(args.project_id)]
    return base


def ssh_command(args):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]+',args.ssh_host) or args.ssh_host.startswith('-'):raise ValueError('SSH_HOST_INVALID')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.ssh_user):raise ValueError('SSH_USER_INVALID')
    return ['ssh','-N','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30',
        '-L',f'127.0.0.1:{args.local_port}:127.0.0.1:{args.remote_port}',
        '-p',str(args.ssh_port),'-i',str(Path(args.identity).expanduser()),args.ssh_user+'@'+args.ssh_host]


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('doctor','build','demo','init-config','init-pdf','check','api','worker','migrate','tunnel','graph-init','graph-projector','graph-rebuild'))
    p.add_argument('--image',default=IMAGE)
    p.add_argument('--project-id')
    p.add_argument('--volume',type=name,default=VOLUME)
    p.add_argument('--poster-volume',type=name)
    p.add_argument('--poster-subdir',default='images')
    p.add_argument('--network',type=name)
    p.add_argument('--container-name',type=name)
    p.add_argument('--env-file',type=Path)
    p.add_argument('--config',type=Path,default=ROOT/'examples/agent_framework/team_config.pdf-mock.json')
    p.add_argument('--port',type=int,default=8012)
    p.add_argument('--once',action='store_true')
    p.add_argument('--revision',choices=('AF001','AF002','AF003','AF004','AF005'),default='AF003')
    p.add_argument('--ssh-host')
    p.add_argument('--ssh-user',default='root')
    p.add_argument('--ssh-port',type=int,default=20719)
    p.add_argument('--identity',default=str(Path.home()/'.ssh/id_ed25519'))
    p.add_argument('--local-port',type=int,default=18015)
    p.add_argument('--remote-port',type=int,default=8015)
    return p


def main(argv=None):
    p=parser();args=p.parse_args(argv)
    for value in (args.port,args.ssh_port,args.local_port,args.remote_port):
        if not 1<=value<=65535:p.error('Port must be between 1 and 65535.')
    if args.action in ('check','api','worker','migrate','graph-init','graph-projector','graph-rebuild') and args.env_file is None:p.error('--env-file is required.')
    if args.action=='tunnel' and not args.ssh_host:p.error('--ssh-host is required.')
    try:
        if args.action=='init-config':
            target=args.env_file or ROOT/'.runtime/framework-desktop.env'
            target.parent.mkdir(parents=True,exist_ok=True)
            write_private(target,(ROOT/'deploy/agent-framework/app.env.example').read_text(encoding='utf-8'))
            print(json.dumps({'created':str(target),'existing_env_overwritten':False}));return 0
        if args.action=='doctor':
            result=subprocess.run(['docker','info','--format','{{.OSType}}'],capture_output=True,text=True,timeout=20)
            if result.returncode or result.stdout.strip()!='linux':raise ValueError('DOCKER_LINUX_ENGINE_REQUIRED')
            print(json.dumps({'docker_engine':'linux','host_platform':sys.platform,'storage':'Linux named volume','model_server_verified':False}));return 0
        if args.action=='tunnel':return subprocess.run(ssh_command(args),shell=False).returncode
        if args.action in ('build','demo','init-pdf'):return subprocess.run(docker_command(args),cwd=ROOT,shell=False).returncode
        with settings(args) as folder:return subprocess.run(docker_command(args,folder),cwd=ROOT,shell=False).returncode
    except KeyboardInterrupt:return 130
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        # Never echo settings, secret values, subprocess output or argv.
        code=str(error) if isinstance(error,ValueError) else type(error).__name__
        print(json.dumps({'error':code}),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
