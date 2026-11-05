"""Plan/check an isolated Qwen server. Only explicit `serve` starts a GPU job.

No package install, weight download, Git change or service termination is automatic.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path,PurePosixPath
import socket
import subprocess
import sys

HERE=Path(__file__).resolve().parent


def load_config(path):
    config=json.loads(Path(path).read_text(encoding='utf-8'))
    if config['host']!='127.0.0.1':raise ValueError('LOOPBACK_REQUIRED')
    for key in ('model_directory','venv_directory','cache_directory'):
        p=PurePosixPath(config[key])
        root='/workspace/creative-checker-a02/models' if key=='model_directory' else '/opt/creative-checker-a02/runtime'
        if not p.is_absolute() or '..' in p.parts or not p.is_relative_to(root) or p==PurePosixPath(root):
            raise ValueError('DEDICATED_PATH_REQUIRED')
    if not 1024<=config['port']<=65535 or not 0<config['gpu_memory_utilization']<1:
        raise ValueError('INVALID_SERVER_LIMIT')
    if config['max_num_seqs']!=1 or not config['language_model_only'] or not config['enforce_eager']:
        raise ValueError('INITIAL_A02_PROFILE_REQUIRED')
    # Old A02-only JSON files remain valid. The pinned Qwen3.5/vLLM profile
    # requires a matching parser whenever automatic tool selection is enabled.
    automatic=config.get('enable_auto_tool_choice',False)
    tool_parser=config.get('tool_call_parser')
    if (type(automatic) is not bool or (automatic and tool_parser!='qwen3_coder')
            or (not automatic and tool_parser is not None)):
        raise ValueError('INVALID_TOOL_CALLING_CONFIGURATION')
    manifest=json.loads((HERE/'model-manifest.json').read_text(encoding='utf-8'))
    if any(config[k]!=manifest[k] for k in ('model','revision')):raise ValueError('MODEL_REVISION_MISMATCH')
    return config


def command(config):
    result=[str(PurePosixPath(config['venv_directory'])/'bin/python'),'-m','vllm.entrypoints.openai.api_server',
        '--model',config['model_directory'],'--served-model-name',config['model'],
        '--host',config['host'],'--port',str(config['port']),'--dtype',config['dtype'],
        '--tensor-parallel-size','1','--max-model-len',str(config['max_model_len']),
        '--max-num-seqs',str(config['max_num_seqs']),'--gpu-memory-utilization',str(config['gpu_memory_utilization']),
        '--language-model-only','--enforce-eager','--reasoning-parser','qwen3',
        '--enable-auto-tool-choice','--tool-call-parser','qwen3_coder',
        '--default-chat-template-kwargs','{"enable_thinking":false}']
    if config.get('enable_auto_tool_choice',False):
        result+=['--enable-auto-tool-choice','--tool-call-parser',config['tool_call_parser']]
    return result


def gpu_rows():
    result=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,memory.free',
        '--format=csv,noheader,nounits'],capture_output=True,text=True,check=True,timeout=10)
    rows=[]
    for line in result.stdout.splitlines():
        name,total,free=(part.strip() for part in line.split(','))
        rows.append({'name':name,'total_mib':int(total),'free_mib':int(free)})
    return rows


def gpu_blockers(config,rows):
    if len(rows)!=1:return ['EXACTLY_ONE_GPU_REQUIRED']
    required=max(config['minimum_free_gpu_mib'],int(rows[0]['total_mib']*config['gpu_memory_utilization'])+256)
    return ['GPU_MEMORY_NOT_AVAILABLE'] if rows[0]['free_mib']<required else []


def verify_model(config):
    manifest=json.loads((HERE/'model-manifest.json').read_text(encoding='utf-8'))
    root=Path(config['model_directory']);issues=[]
    for record in manifest['files']:
        path=root/record['name']
        if not path.is_file():issues.append('MISSING_MODEL_FILE:'+record['name']);continue
        if path.stat().st_size!=record['size']:issues.append('MODEL_SIZE_MISMATCH:'+record['name']);continue
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(8*1024*1024),b''):digest.update(block)
        if digest.hexdigest()!=record['sha256']:issues.append('MODEL_SHA256_MISMATCH:'+record['name'])
    return issues


def check(config,*,full_hash=False):
    blockers=[];rows=[]
    try:rows=gpu_rows();blockers+=gpu_blockers(config,rows)
    except (OSError,ValueError,subprocess.SubprocessError):blockers.append('GPU_QUERY_FAILED')
    try:
        with socket.socket() as sock:sock.bind((config['host'],config['port']))
    except OSError:blockers.append('SERVER_PORT_UNAVAILABLE')
    expected=config['runtime_candidate'].removeprefix('vllm==')
    if Path(sys.prefix).resolve()!=Path(config['venv_directory']).resolve():blockers.append('ISOLATED_VENV_REQUIRED')
    try:
        if importlib.metadata.version('vllm')!=expected:blockers.append('VLLM_VERSION_MISMATCH')
    except importlib.metadata.PackageNotFoundError:blockers.append('VLLM_NOT_INSTALLED')
    if len(os.environ.get(config['api_key_environment'],''))<32:blockers.append('SERVER_API_KEY_REQUIRED')
    if not Path(command(config)[0]).is_file():blockers.append('ISOLATED_PYTHON_MISSING')
    if full_hash:blockers+=verify_model(config)
    elif not Path(config['model_directory']).is_dir():blockers.append('MODEL_DIRECTORY_MISSING')
    return {'gpu':rows,'blockers':blockers,'model_sha256_checked':full_hash,'server_started':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan','check','serve'))
    parser.add_argument('--config',type=Path,default=HERE/'server.json')
    parser.add_argument('--verify-model',action='store_true',help='Read/hash pinned model files; never download them.')
    args=parser.parse_args(argv)
    config=load_config(args.config)
    if args.action=='plan':
        print(json.dumps({'configuration':config,'command':command(config),'server_started':False,
            'packages_installed':False,'models_downloaded':False},ensure_ascii=False,indent=2));return 0
    result=check(config,full_hash=args.verify_model or args.action=='serve')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    if result['blockers']:return 2
    if args.action=='check':return 0
    env=os.environ.copy();env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
        VLLM_CACHE_ROOT=config['cache_directory'],TORCHINDUCTOR_CACHE_DIR=config['cache_directory']+'/torchinductor')
    # Foreground exec preserves signals; no daemon, auto-restart or auto-retry.
    os.execve(command(config)[0],command(config),env)


if __name__=='__main__':raise SystemExit(main())
