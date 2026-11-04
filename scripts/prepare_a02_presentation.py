"""Prepare separate local A02/front-v2 settings; never overwrite or start services."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from scripts.framework_desktop import read_env
from backend.agent_framework.composition import TeamConfiguration
from backend.app.v1_config import load_v1_settings

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_ORIGINS=('http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:8000','http://localhost:8000')


def prepare(env_file,config_file,output_env,output_config,*,origins=DEFAULT_ORIGINS):
    outputs=(Path(output_env),Path(output_config))
    if outputs[0].resolve()==outputs[1].resolve() or any(path.exists() for path in outputs):
        raise ValueError('Output already exists; original and prepared files are never overwritten.')
    values=read_env(env_file)
    if any(values.get(key) for key in ('DB_OWNER_PASSWORD','POSTGRES_PASSWORD')):
        raise ValueError('Use the existing app-only environment file.')
    settings=load_v1_settings(values)
    if settings.environment not in ('development','test'):raise ValueError('Local development/test only.')
    config=TeamConfiguration.model_validate_json(Path(config_file).read_text(encoding='utf-8-sig'))
    if config.pdf_extraction is None or config.pdf_extraction.mode!='qwen':raise ValueError('An existing Qwen configuration is required.')
    config=config.model_copy(update={'pdf_extraction':config.pdf_extraction.model_copy(update={
        'structuring':'semantic','presentation':True,'max_output_tokens':16384})})
    config=TeamConfiguration.model_validate(config.model_dump(mode='python'))
    existing=[value.strip() for value in values.get('AUTH_ALLOWED_ORIGINS','').split(',') if value.strip()]
    values['AUTH_ALLOWED_ORIGINS']=','.join(dict.fromkeys([*existing,*origins]))
    hosts=[value.strip() for value in values.get('AUTH_ALLOWED_HOSTS',','.join(settings.hosts)).split(',') if value.strip()]
    values['AUTH_ALLOWED_HOSTS']=','.join(dict.fromkeys([*hosts,*(urlsplit(value).hostname or '' for value in origins)]))
    load_v1_settings(values)
    payloads=(''.join(key+'='+value+'\n' for key,value in values.items()),config.model_dump_json(indent=2)+'\n')
    for path,payload in zip(outputs,payloads):
        path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:stream.write(payload)
    return {'env_file':str(outputs[0]),'config_file':str(outputs[1]),'presentation':True,'frontend_origins':list(origins)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output-env',type=Path,default=ROOT/'.runtime/framework-desktop-presentation.env')
    parser.add_argument('--output-config',type=Path,default=ROOT/'.runtime/team_config.qwen-presentation.json')
    parser.add_argument('--frontend-origin',action='append')
    args=parser.parse_args()
    print(json.dumps(prepare(args.env_file,args.config,args.output_env,args.output_config,
        origins=tuple(args.frontend_origin) if args.frontend_origin else DEFAULT_ORIGINS),ensure_ascii=False))


if __name__=='__main__':main()
