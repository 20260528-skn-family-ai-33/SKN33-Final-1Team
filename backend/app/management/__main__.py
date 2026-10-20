"""Explicit owner-only account management. No password argument, automatic seed or migration."""
import argparse,getpass,json,os,sys
from pathlib import Path
from uuid import UUID
from ..config import ConfigurationError,read_environment_file
from ..persistence.database import PersistenceError
from ..security.errors import AuthError
from .io import OpsError


class SafeParser(argparse.ArgumentParser):
    def error(self,message):raise AuthError('INVALID_MANAGEMENT_REQUEST',422)


def main(argv=None):
    engine=None
    try:
        parser=SafeParser(description=__doc__)
        parser.add_argument('--env-file',type=Path,required=True,help='Explicit owner-only literal environment file.')
        commands=parser.add_subparsers(dest='command',required=True,parser_class=SafeParser)
        create=commands.add_parser('create-email-user');create.add_argument('--email',required=True);create.add_argument('--display-name',required=True)
        reset=commands.add_parser('reset-password');reset.add_argument('--user-id',type=UUID,required=True)
        change=commands.add_parser('change-email');change.add_argument('--user-id',type=UUID,required=True);change.add_argument('--email',required=True)
        active=commands.add_parser('set-user-active');active.add_argument('--user-id',type=UUID,required=True);active.add_argument('--active',choices=('true','false'),required=True)
        member=commands.add_parser('set-project-member');member.add_argument('--project-id',type=UUID,required=True);member.add_argument('--user-id',type=UUID,required=True)
        member.add_argument('--role',choices=('owner','editor','viewer'),required=True);member.add_argument('--active',choices=('true','false'),required=True)
        commands.add_parser('bootstrap-development')
        from .operations import parsers,COMMANDS,run
        parsers(commands)
        args=parser.parse_args(argv)
        from ..security.logging import configure_security_logging
        configure_security_logging()
        values=read_environment_file(args.env_file)
        values.pop('CC_MIGRATION_ENV_FILE',None)  # --env-file is the only selected management file.
        from ...migrations.configuration import load_migration_settings,create_migration_engine,validate_migration_connection
        from ..persistence.schema_inventory import validate_schema
        from ..persistence.transaction import unit_of_work
        from ..security.passwords import Passwords
        from .accounts import AccountManager
        settings=load_migration_settings(values);engine=create_migration_engine(settings)
        if args.command!='inspect-backup':
            with unit_of_work(engine) as connection:
                validate_migration_connection(connection,settings)
                if args.command!='restore':validate_schema(connection,app_user=settings.app_user)
        if args.command in COMMANDS:
            result=run(args,values,engine,settings)
            print(json.dumps({'status':'completed','result':result},ensure_ascii=False));return 0
        manager=AccountManager(engine,Passwords(),operator=getpass.getuser())
        password=None
        if args.command in ('create-email-user','reset-password'):
            if not sys.stdin.isatty():raise AuthError('INTERACTIVE_PASSWORD_REQUIRED',422)
            password=getpass.getpass('새 비밀번호: ')
            if password!=getpass.getpass('새 비밀번호 확인: '):raise AuthError('INVALID_AUTH_REQUEST',422)
        if args.command=='create-email-user':result=manager.create_email_user(args.email,args.display_name,password)
        elif args.command=='reset-password':result=manager.reset_password(args.user_id,password)
        elif args.command=='change-email':result=manager.change_email(args.user_id,args.email)
        elif args.command=='set-user-active':result=manager.set_user_active(args.user_id,args.active=='true')
        elif args.command=='set-project-member':result=manager.set_project_member(args.project_id,args.user_id,args.role,args.active=='true')
        else:
            try:project=UUID(values['DEV_PROJECT_ID'])
            except (KeyError,ValueError):raise AuthError('INVALID_AUTH_REQUEST',422) from None
            result=manager.bootstrap_development(project,environment=values.get('APP_ENV','production'),enabled=values.get('LOCAL_ADMIN_LOGIN')=='1')
        print(json.dumps({'status':'completed','result':result},ensure_ascii=False));return 0
    except (ConfigurationError,PersistenceError,AuthError) as error:
        print(json.dumps({'status':'error','code':error.code,'message':str(error)},ensure_ascii=False));return 1
    except OpsError as error:
        print(json.dumps({'status':'error','code':error.code,'message':'관리 상태와 고정 입력을 확인해 주세요.'},ensure_ascii=False));return 1
    except Exception:
        print(json.dumps({'status':'error','code':'MANAGEMENT_FAILED','message':'관리 작업을 완료하지 못했습니다.'},ensure_ascii=False));return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
