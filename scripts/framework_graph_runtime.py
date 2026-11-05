"""Start/status/stop the persistent development Neo4j + projection worker.

Standard-library host script for Mac/Windows Docker Desktop Linux. Original env
files and the existing graph volume stay unchanged. No automatic migrations,
graph initialization, deletion, model calls or human approvals.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.framework_desktop import read_env,write_private,mount_file

PROJECT='creative-checker-agent-framework'
DATABASE_KEYS=set('PERSISTENCE_MODE DB_HOST DB_PORT POSTGRES_DB DB_APP_USER DB_APP_PASSWORD DB_OWNER_USER DB_CONNECT_TIMEOUT DB_POOL_SIZE DB_POOL_TIMEOUT APP_ENV'.split())
STATE=ROOT/'.runtime/framework-graph-runtime'


class RuntimeErrorCode(RuntimeError):pass


def worker_values(app,graph):
    if any(app.get(k) for k in ('DB_OWNER_PASSWORD','POSTGRES_PASSWORD')):
        raise RuntimeErrorCode('APP_CREDENTIALS_ONLY')
    required=('DB_HOST','DB_PORT','POSTGRES_DB','DB_APP_USER','DB_APP_PASSWORD')
    if any(not app.get(k) for k in required) or not graph.get('AF_NEO4J_PASSWORD'):
        raise RuntimeErrorCode('RUNTIME_SETTINGS_INCOMPLETE')
    if app.get('CC_NEO4J_PASSWORD') and app['CC_NEO4J_PASSWORD']!=graph['AF_NEO4J_PASSWORD']:
        raise RuntimeErrorCode('GRAPH_PASSWORD_SETTINGS_DIFFER')
    values={k:v for k,v in app.items() if k in DATABASE_KEYS}
    values.setdefault('PERSISTENCE_MODE','postgres');values.setdefault('APP_ENV','development')
    if values['PERSISTENCE_MODE']!='postgres' or values['APP_ENV']!='development':
        raise RuntimeErrorCode('DEVELOPMENT_POSTGRES_PROFILE_REQUIRED')
    if values['DB_HOST'] in ('localhost','127.0.0.1','::1'):values['DB_HOST']='host.docker.internal'
    values.update(CC_NEO4J_URI='bolt://neo4j:7687',CC_NEO4J_USER='neo4j',
        CC_NEO4J_PASSWORD=graph['AF_NEO4J_PASSWORD'])
    return values


def command(argv,*,capture=True,allowed_codes=(0,)):
    # Do not let inherited COMPOSE_* select an unrelated project or config.
    env={k:v for k,v in os.environ.items() if not k.startswith(('COMPOSE_','AF_'))}
    try:
        result=subprocess.run(argv,cwd=ROOT,env=env,shell=False,capture_output=capture,text=True,timeout=180)
    except (OSError,subprocess.SubprocessError):raise RuntimeErrorCode('DOCKER_COMMAND_UNAVAILABLE') from None
    if result.returncode not in allowed_codes:raise RuntimeErrorCode('DOCKER_COMMAND_FAILED')
    return result.stdout.strip() if capture else ''


def prepare(app_file,graph_file,image,*,state=STATE):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./:_@-]{0,240}',image):raise RuntimeErrorCode('IMAGE_NAME_INVALID')
    app=read_env(app_file);graph=read_env(graph_file);values=worker_values(app,graph)
    state.mkdir(parents=True,exist_ok=True)
    def compose_text(folder):
        compose={'AF_NEO4J_PASSWORD':graph['AF_NEO4J_PASSWORD'],'AF_FRAMEWORK_IMAGE':image,
            'AF_GRAPH_APP_ENV_FILE':str((folder/'worker.env').resolve())}
        return ''.join(k+"='"+v.replace("'","\\'")+"'\n" for k,v in compose.items())
    if (state/'current.json').exists():
        previous=current(state=state)
        if read_env(previous/'worker.env')==values and (previous/'compose.env').read_text(encoding='utf-8')==compose_text(previous):
            return previous
    folder=state/('run-'+secrets.token_hex(8));folder.mkdir(mode=0o700)
    app_target=folder/'worker.env'
    write_private(app_target,''.join(k+'='+v+'\n' for k,v in values.items()))
    # Compose single quotes preserve literal dollars, Windows paths and spaces.
    write_private(folder/'compose.env',compose_text(folder))
    return folder


def compose_argv(folder,*args):
    result=['docker','compose','--project-name',PROJECT,'--env-file',str(folder/'compose.env'),
        '--file',str(ROOT/'deploy/agent-framework/compose.neo4j.yaml'),
        '--file',str(ROOT/'deploy/agent-framework/compose.runtime.yaml')]
    if sys.platform.startswith('linux'):result+=['--file',str(ROOT/'deploy/agent-framework/compose.host-linux.yaml')]
    return result+list(args)


def preflight(folder,image):
    args=['docker','run','--rm']
    if sys.platform.startswith('linux'):args+=['--add-host','host.docker.internal:host-gateway']
    args+=mount_file(folder/'worker.env','/run/framework/app.env')
    args+=[image,'postgres-check','--env-file','/run/framework/app.env']
    result=json.loads(command(args))
    if result.get('revision')!='AF005':raise RuntimeErrorCode('APPLY_AF005_EXPLICITLY_FIRST')


def remember(folder,*,state=STATE):
    target=state/'current.json';temporary=state/('current-'+secrets.token_hex(4)+'.json')
    write_private(temporary,json.dumps({'version':1,'project':PROJECT,'folder':folder.name})+'\n')
    os.replace(temporary,target)


def current(*,state=STATE):
    value=json.loads((state/'current.json').read_text(encoding='utf-8'))
    if value.get('version')!=1 or value.get('project')!=PROJECT or not re.fullmatch(r'run-[0-9a-f]{16}',value.get('folder','')):
        raise RuntimeErrorCode('RUNTIME_STATE_INVALID')
    folder=state/value['folder']
    if folder.resolve().parent!=state.resolve() or not (folder/'compose.env').is_file():
        raise RuntimeErrorCode('RUNTIME_STATE_INVALID')
    return folder


def monitor_summary(value):
    metrics=value.get('metrics');known=isinstance(metrics,dict)
    projects=metrics.get('projects',[]) if known else []
    summary={k:value.get(k) for k in ('state','observed_at','alerts')}
    summary.update(pending_projects=sum(p['pending_events']>0 or p['required_revision']>p['applied_revision'] for p in projects) if known else None,
        pending_events=sum(p['pending_events'] for p in projects) if known else None,
        max_pending_seconds=max((p['oldest_pending_seconds'] for p in projects),default=0) if known else None,
        recent_sql_fallbacks=metrics.get('recent_sql_fallbacks') if known else None)
    return summary


def status(folder):
    services=[]
    for service in ('neo4j','projector','monitor','recovery'):
        identity=command(compose_argv(folder,'ps','--all','--quiet',service))
        if not identity:
            services.append({'service':service,'running':False,'health':'missing'});continue
        raw=command(['docker','inspect','--format',
            '{"running":{{.State.Running}},"restart":"{{.HostConfig.RestartPolicy.Name}}","count":{{.RestartCount}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"unavailable"{{end}}}',identity])
        services.append({'service':service,**json.loads(raw)})
    try:
        synchronization=json.loads(command(compose_argv(folder,'exec','-T','monitor','/app/.venv/bin/python',
            '-m','backend.agent_framework','graph-monitor-status','--state-file','/data/monitor/latest.json'),allowed_codes=(0,1)))
    except (RuntimeErrorCode,ValueError):synchronization={'state':'unknown','alerts':[{'code':'MONITOR_STATUS_UNAVAILABLE'}]}
    summary=monitor_summary(synchronization)
    try:
        recovery=json.loads(command(compose_argv(folder,'exec','-T','recovery','/app/.venv/bin/python',
            '-m','backend.agent_framework','graph-recovery-status','--state-file','/data/recovery/latest.json'),allowed_codes=(0,1)))
    except (RuntimeErrorCode,ValueError):recovery={'state':'unknown','reason':'RECOVERY_STATUS_UNAVAILABLE'}
    repair={k:recovery.get(k) for k in ('state','observed_at','automatic_repair','actions','issues','reason')}
    repair['confirming_projects']=len(recovery.get('candidates',{}))
    print(json.dumps({'project':PROJECT,'services':services,'synchronization':summary,'recovery':repair}),flush=True)
    return 0 if all(s['running'] and s['health']=='healthy' for s in services) and summary['state']=='healthy' and repair['state']=='healthy' else 1


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('up','status','monitor','stop'))
    p.add_argument('--env-file',type=Path,default=ROOT/'.runtime/framework-graph-app.env')
    p.add_argument('--graph-env-file',type=Path,default=ROOT/'.runtime/framework-graph-dev.env')
    p.add_argument('--image',default='creative-checker-framework:graph-recovery-20261104')
    args=p.parse_args(argv)
    try:
        if args.action=='up':
            if command(['docker','info','--format','{{.OSType}}'])!='linux':raise RuntimeErrorCode('DOCKER_LINUX_ENGINE_REQUIRED')
            folder=prepare(args.env_file,args.graph_env_file,args.image)
            preflight(folder,args.image);remember(folder)
            command(compose_argv(folder,'up','--detach','--wait','--wait-timeout','150','neo4j','projector','monitor','recovery'))
            return status(folder)
        folder=current()
        if args.action=='status':return status(folder)
        if args.action=='monitor':
            value=json.loads(command(compose_argv(folder,'exec','-T','monitor','/app/.venv/bin/python',
                '-m','backend.agent_framework','graph-monitor','--env-file','/run/framework/app.env','--once'),allowed_codes=(0,1)))
            print(json.dumps(value),flush=True);return 0 if value['state']=='healthy' else 1
        command(compose_argv(folder,'stop','recovery','monitor','projector','neo4j'))
        print(json.dumps({'project':PROJECT,'stopped':True,'volumes_preserved':True}));return 0
    except KeyboardInterrupt:return 130
    except (OSError,ValueError,RuntimeErrorCode) as error:
        code=str(error) if isinstance(error,RuntimeErrorCode) else 'RUNTIME_SETTINGS_UNAVAILABLE'
        print(json.dumps({'error':code,'message':'Check the explicit app settings and image; no migration or initialization was performed.'}));return 2


if __name__=='__main__':raise SystemExit(main())
