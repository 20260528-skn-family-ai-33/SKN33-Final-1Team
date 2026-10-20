"""C14 owner-only commands; existing account CLI remains unchanged."""
from pathlib import Path
from uuid import UUID
from ..config import read_environment_file
from ..storage.local import LocalStorage
from .io import OpsError,source_tree

COMMANDS={'import-dry-run','import-apply','gc','check-files','repair-file','backup','inspect-backup','restore','restore-release','release-check'}


def parsers(commands):
    dry=commands.add_parser('import-dry-run');dry.add_argument('--root',type=Path,required=True);dry.add_argument('--source',type=Path,required=True)
    dry.add_argument('--project-id',type=UUID,required=True);dry.add_argument('--actor-user-id',type=UUID,required=True)
    dry.add_argument('--namespace',required=True);dry.add_argument('--display-name',required=True)
    dry.add_argument('--existing-image-id',type=UUID);dry.add_argument('--criteria-set-id',type=UUID)
    apply=commands.add_parser('import-apply');apply.add_argument('--root',type=Path,required=True);apply.add_argument('--source',type=Path,required=True)
    apply.add_argument('--plan-id',type=UUID,required=True);apply.add_argument('--actor-user-id',type=UUID,required=True);apply.add_argument('--revalidate',action='store_true')
    gc=commands.add_parser('gc');gc.add_argument('--root',type=Path,required=True);gc.add_argument('--apply',action='store_true');gc.add_argument('--delete',action='store_true')
    check=commands.add_parser('check-files');check.add_argument('--root',type=Path,required=True);check.add_argument('--mark-missing',action='store_true')
    repair=commands.add_parser('repair-file');repair.add_argument('--root',type=Path,required=True);repair.add_argument('--file-id',type=UUID,required=True);repair.add_argument('--replacement',type=Path,required=True)
    backup=commands.add_parser('backup');backup.add_argument('--root',type=Path,required=True);backup.add_argument('--output',type=Path,required=True)
    backup.add_argument('--app-env-file',type=Path,required=True);backup.add_argument('--drain-seconds',type=int,default=900);backup.add_argument('--legacy-source',type=Path)
    inspect=commands.add_parser('inspect-backup');inspect.add_argument('--backup',type=Path,required=True)
    restore=commands.add_parser('restore');restore.add_argument('--backup',type=Path,required=True);restore.add_argument('--new-root',type=Path,required=True);restore.add_argument('--app-env-file',type=Path,required=True)
    release=commands.add_parser('restore-release');release.add_argument('--root',type=Path,required=True);release.add_argument('--app-env-file',type=Path,required=True)
    check_release=commands.add_parser('release-check');check_release.add_argument('--root',type=Path,required=True)
    check_release.add_argument('--backup',type=Path,required=True);check_release.add_argument('--app-env-file',type=Path,required=True);check_release.add_argument('--worker-env-file',type=Path,required=True)


def run(args,values,engine,settings):
    from .imports import Imports
    from .files import FileOperations
    from .backup import Backups,PgTools
    allowed=values.get('IMPORT_ALLOWED_ROOT')
    if args.command.startswith('import-') or args.command=='repair-file':
        if not allowed:raise OpsError('IMPORT_ALLOWED_ROOT_REQUIRED')
    if args.command=='inspect-backup':
        manifest=Backups(engine,settings,None).inspect(args.backup)
        return {key:manifest[key] for key in ('backup_id','state','restore_ready','schema_revision','pending')}
    if args.command=='restore':return Backups(engine,settings,PgTools(settings,values)).restore(args.backup,args.new_root,read_environment_file(args.app_env_file,{}))
    storage=LocalStorage(args.root)
    try:
        if args.command not in ('backup','restore-release','release-check'):storage.start(shared=True,maintenance=args.command in ('check-files','repair-file'))
        if args.command=='release-check':
            from .release import check
            return check(engine,settings,storage,args.backup,read_environment_file(args.app_env_file,{}),read_environment_file(args.worker_env_file,{}))
        if args.command=='import-dry-run':
            plan=Imports(engine,storage).dry_run(args.source,allowed,project_id=args.project_id,actor_id=args.actor_user_id,
                namespace=args.namespace,display_name=args.display_name,existing_image_id=args.existing_image_id,criteria_set_id=args.criteria_set_id)
            return {key:plan[key] for key in ('operation_id','state','package_fingerprint','reasons','preview')}
        if args.command=='import-apply':return Imports(engine,storage).apply(args.plan_id,args.source,allowed,actor_id=args.actor_user_id,revalidate=args.revalidate)
        if args.command=='gc':
            if args.delete and not args.apply:raise OpsError('GC_DELETE_REQUIRES_APPLY')
            return FileOperations(engine,storage).collect(apply=args.apply,delete=args.delete)
        if args.command=='check-files':return FileOperations(engine,storage).check(mark_missing=args.mark_missing)
        if args.command=='repair-file':
            with source_tree(args.replacement.parent,allowed) as tree:raw=tree.bytes(args.replacement.name,512*1024*1024)
            return FileOperations(engine,storage).repair(args.file_id,raw)
        if args.command=='backup':
            manifest=Backups(engine,settings,PgTools(settings,values)).capture(storage,args.output,read_environment_file(args.app_env_file,{}),drain_seconds=args.drain_seconds,legacy_source=args.legacy_source)
            return {key:manifest[key] for key in ('backup_id','state','restore_ready','capture_seconds','pending')}
        if args.command=='restore-release':return Backups(engine,settings,None).release(storage,read_environment_file(args.app_env_file,{}))
        raise OpsError('UNKNOWN_OPERATIONS_COMMAND')
    finally:storage.close()
