"""Offline custom-format DB + private files backup, fresh-target restore and full validation."""
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,re,subprocess,time
from uuid import UUID,uuid4
from sqlalchemy import select,update,func,CheckConstraint
from ..persistence.transaction import unit_of_work
from ..persistence.schema import metadata
from ..persistence.schema_inventory import validate_schema,capture_structure,capture_privileges,_load_contract
from ..services.business import table,one
from ..services.reviews import input_payload
from ..hashing.canonical import digest
from ..storage.local import LocalStorage
from .io import Tree,OpsError,json_bytes,strict_json,verify_bytes
from .maintenance import offline,frozen_database
from .files import audit
from .imports import projection_fields

MAX_BACKUP_FILES=10000
MAX_BACKUP_BYTES=4*1024**3


class PgTools:
    """Native PostgreSQL 17 client directory or an explicit already-present Docker image."""
    def __init__(self,settings,values):
        self.settings=settings;self.mode=values.get('PG_TOOLS_MODE')
        if self.mode=='docker':
            if values.get('PG_TOOLS_IMAGE')!='postgres:17.11-bookworm':raise OpsError('PG_TOOL_IMAGE_REQUIRED')
            self.prefix=['docker','run','--rm','--pull=never','-i','--add-host','host.docker.internal:host-gateway',
                         '--env','PGPASSWORD','--env','PGCONNECT_TIMEOUT','--env','PGTZ','postgres:17.11-bookworm']
            self.host=values.get('PG_TOOLS_HOST',settings.host);self.port=values.get('PG_TOOLS_PORT',str(settings.port))
        elif self.mode=='native':
            folder=Path(values.get('PG_TOOLS_BIN',''))
            if not folder.is_absolute() or any(not (folder/tool).is_file() for tool in ('pg_dump','pg_restore')):raise OpsError('PG_TOOLS_BIN_REQUIRED')
            self.prefix=[];self.folder=folder;self.host=settings.host;self.port=str(settings.port)
        else:raise OpsError('EXPLICIT_PG_TOOLS_REQUIRED')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]+',self.host) or not self.port.isdecimal() or not 1<=int(self.port)<=65535:raise OpsError('PG_TOOL_TARGET_INVALID')

    def run(self,tool,args,*,stdin=None,stdout=None,timeout=900):
        name='cc-pgtools-'+uuid4().hex
        executable=[str(self.folder/tool)] if self.mode=='native' else [*self.prefix[:-1],'--name',name,self.prefix[-1],tool]
        env={key:value for key,value in os.environ.items() if not key.startswith(('DB_','POSTGRES_','TEST_DB_','AUTH_')) and key not in ('CSRF_SECRET','CURSOR_SECRET')}
        env.update(PGPASSWORD=self.settings.owner_password.reveal(),PGCONNECT_TIMEOUT='5',PGTZ='UTC')
        try:result=subprocess.run([*executable,*args],stdin=stdin,stdout=stdout or subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=timeout)
        except subprocess.TimeoutExpired:
            if self.mode=='docker':subprocess.run(['docker','rm','--force',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
            raise OpsError('PG_TOOL_TIMEOUT') from None
        if result.returncode!=0:raise OpsError('PG_TOOL_FAILED')
        return result.stdout

    def version(self,tool):
        value=self.run(tool,['--version']).decode('ascii').strip()
        if not re.fullmatch(r'pg_(dump|restore) \(PostgreSQL\) 17\.[0-9]+[^\r\n]*',value):raise OpsError('PG_TOOL_VERSION_MISMATCH')
        return value

    def connection(self):
        return ['--no-password','--host',self.host,'--port',self.port,'--username',self.settings.owner_user,'--dbname',self.settings.database]


def secret_hashes(values):
    from ..v1_config import load_v1_settings
    settings=load_v1_settings(values)
    return settings,{name:hashlib.sha256(values[name].encode()).hexdigest() for name in ('CSRF_SECRET','CURSOR_SECRET')}


def snapshot(c):
    """Order-independent exact row digests; no credential/row contents leave this function."""
    quote=c.dialect.identifier_preparer.quote;result={}
    tables=c.exec_driver_sql("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%' ORDER BY 1,2").all()
    for schema,name in tables:
        hashes=[]
        rows=c.exec_driver_sql('SELECT row_to_json(t)::text FROM '+quote(schema)+'.'+quote(name)+' t')
        for row in rows:
            if len(hashes)>=1000000:raise OpsError('BACKUP_ROW_LIMIT_EXCEEDED')
            hashes.append(hashlib.sha256(row[0].encode()).digest())
        result[schema+'.'+name]=dict(rows=len(hashes),sha256=hashlib.sha256(b''.join(sorted(hashes))).hexdigest())
    return result


def restore_check_rendering(c,app_user):
    """pg_dump reparses VARCHAR IN arrays as element casts instead of an array cast.

    Accept exactly that observed spelling at this restore-only boundary, then
    reinstall the original reviewed CHECK text WITH validation. Runtime schema
    checks remain byte-exact; every other catalog/privilege difference is fatal.
    """
    contract=_load_contract();expected=contract['structure'];actual=capture_structure(c)
    if any(actual[name]!=value for name,value in expected.items() if name!='constraints') or capture_privileges(c,app_user)!=contract['privileges']:
        raise OpsError('RESTORE_SCHEMA_DIFFERENCE')
    if len(actual['constraints'])!=len(expected['constraints']):raise OpsError('RESTORE_SCHEMA_DIFFERENCE')
    pattern=re.compile(r"ARRAY\[((?:'(?:[^']|'')*'::character varying)(?:, '(?:[^']|'')*'::character varying)*)\]::text\[\]")
    changes=[]
    for want,have in zip(expected['constraints'],actual['constraints']):
        if want==have:continue
        rendered=pattern.sub(lambda match:'ARRAY['+match[1].replace('::character varying','::character varying::text')+']',want[3])
        if want[2]!='c' or want[:3]!=have[:3] or want[4:]!=have[4:] or rendered!=have[3]:raise OpsError('RESTORE_SCHEMA_DIFFERENCE')
        target=table(want[0]);constraint=next((v for v in target.constraints if isinstance(v,CheckConstraint) and v.name==want[1]),None)
        if constraint is None:raise OpsError('RESTORE_SCHEMA_DIFFERENCE')
        changes.append((target,constraint))
    quote=c.dialect.identifier_preparer.quote
    for target,constraint in changes:
        c.exec_driver_sql('ALTER TABLE '+target.fullname+' DROP CONSTRAINT '+quote(constraint.name)+', ADD CONSTRAINT '+quote(constraint.name)+' CHECK ('+str(constraint.sqltext)+')')
    return len(changes)


def graph_check(c,storage,app_user):
    """Recheck every existing FK, lineage, seal, selected graph and service/import hash."""
    schema=validate_schema(c,app_user=app_user);quote=c.dialect.identifier_preparer.quote
    for child in metadata.tables.values():
        for fk in child.foreign_key_constraints:
            elements=list(fk.elements);target=elements[0].column.table
            columns=[(quote(e.parent.name),quote(e.column.name)) for e in elements]
            sql='SELECT 1 FROM '+child.fullname+' ch WHERE '+' AND '.join('ch.'+a+' IS NOT NULL' for a,b in columns)
            sql+=' AND NOT EXISTS(SELECT 1 FROM '+target.fullname+' pa WHERE '+' AND '.join('pa.'+b+'=ch.'+a for a,b in columns)+') LIMIT 1'
            if c.exec_driver_sql(sql).first():raise OpsError('RESTORE_FOREIGN_KEY_MISMATCH')
    for name,column,function in [('projects','project_id','owner'),('image_coordinate_frames','frame_id','frame'),('criteria_versions','criteria_version_id','criteria'),
        ('ocr_runs','ocr_run_id','ocr'),('poster_extractions','extraction_id','extraction'),('evidence_records','evidence_id','evidence'),('review_runs','run_id','review'),('import_packages','import_id','import')]:
        for identifier in c.execute(select(table(name).c[column])).scalars():c.exec_driver_sql('SELECT public.cc_check_'+function+'(%s)',(identifier,))
    for name,column in [('assets','asset_id'),('stored_files','file_id'),('image_coordinate_frames','frame_id'),('import_packages','import_id'),('poster_extractions','extraction_id'),('criteria_versions','criteria_version_id'),('review_runs','run_id')]:
        for identifier in c.execute(select(table(name).c[column])).scalars():c.exec_driver_sql('SELECT public.cc_check_lineage(%s,%s)',(name,identifier))
    checked_service=checked_import=0
    for version in c.execute(select(table('criteria_versions'))).mappings():
        if version['origin_kind']!='service_confirmation':continue
        rows=c.execute(select(table('criteria_items')).where(table('criteria_items').c.criteria_version_id==version['criteria_version_id']).order_by(table('criteria_items').c.position)).mappings().all()
        normalized=[dict(external_item_id=row['external_item_id'],position=row['position'],category=row['category'],text=row['text'],source_text=row['source_text'],rule_code=row['rule_code'],field_code=row['field_code'],expected_value=row['expected_value'],
            is_required=row['is_required'],source_asset_version_id=str(row['source_asset_version_id']) if row['source_asset_version_id'] else None,source_locator=row['source_locator'],applicability=row['applicability_jsonb'],check_scope=row['check_scope_jsonb'],source_item=row['source_item_jsonb']) for row in rows]
        payload=dict(project_id=str(version['project_id']),criteria_set_id=str(version['criteria_set_id']),image_id=str(version['target_asset_version_id']),raw_requirements=version['source_requirements_text'],source_documents=version['source_documents_jsonb'],items=normalized)
        if digest('criteria_snapshot',payload)!=version['criteria_hash']:raise OpsError('RESTORE_CRITERIA_HASH_MISMATCH')
    for run in c.execute(select(table('review_runs'))).mappings():
        if run['origin_kind']!='service_execution':continue
        image=one(c,'asset_versions','asset_version_id',run['asset_version_id']);display=one(c,'stored_files','file_id',image['display_file_id']);version=one(c,'criteria_versions','criteria_version_id',run['criteria_version_id'])
        if digest('review_input',input_payload(run['project_id'],image,display,version,run['requested_config'],workflow=run['workflow_version'],retry=run['retry_policy_jsonb']),'cc-manifest-jcs-v1')!=run['input_manifest_hash']:raise OpsError('RESTORE_INPUT_HASH_MISMATCH')
        if run['execution_status']!='completed':continue
        op=str(run['selected_attempt_id'])
        with storage.read('operations/'+op+'/journal.json') as stream:intent=strict_json(stream.read(262145))
        with storage.read('staging/'+op+'/plan.json') as stream:raw=stream.read(48*1024*1024+1)
        if hashlib.sha256(raw).hexdigest()!=intent['plan_sha256']:raise OpsError('RESTORE_RESULT_PLAN_MISMATCH')
        plan=strict_json(raw,max_bytes=48*1024*1024)
        manifest=dict(run_id=str(run['run_id']),selected_attempt_id=op,execution_status='completed',overall_verdict=plan['overall_verdict'],aggregation_policy_version=run['aggregation_policy_version'],input_manifest_hash=run['input_manifest_hash'],
            ocr=dict(ocr_run_id=plan['ocr_id'],ocr_input_sha256=plan['files'][0]['sha256'],source_json_sha256=plan['files'][1]['sha256'],records=plan['records']),
            extraction=dict(extraction_id=plan['extraction_id'],source_json_sha256=plan['files'][2]['sha256'],observations=plan['observations']),checker_source_sha256=plan['files'][3]['sha256'],items=plan['items'])
        if digest('review_result',manifest,'cc-manifest-jcs-v1')!=run['result_manifest_hash']:raise OpsError('RESTORE_RESULT_HASH_MISMATCH')
        if plan['overall_verdict']!=run['overall_verdict'] or plan['attempt_id']!=op:raise OpsError('RESTORE_RESULT_GRAPH_MISMATCH')
        def compare(name,pk,values,fields):
            for value in values:
                row=one(c,name,pk,UUID(value[pk if pk in value else 'id']))
                if any(row[column]!=value[field] for column,field in fields):raise OpsError('RESTORE_RESULT_GRAPH_MISMATCH')
        compare('ocr_tokens','token_id',plan['records'],[(k,k) for k in ('external_token_id','external_token_type','token_order','text','confidence','bbox_xyxy','polygon','recognition_engine','requires_review')]+[('source_record_jsonb','source_record')])
        compare('item_results','result_id',plan['items'],[(k,k) for k in ('execution_status','verdict','observed_value','reason','method_code','checker_version')]+[('limitations_jsonb','limitations')])
        compare('poster_field_observations','observation_id',plan['observations'],[(k,k) for k in ('field_code','field_path','presence_status','resolution_status','raw_text','normalized_value','source_status','source_reason')])
        evidence=[ev for item in plan['items']+plan['observations'] for ev in item['evidence']]
        compare('evidence_records','evidence_id',evidence,[(k,k) for k in ('source_kind','text','bbox_xyxy','polygon','confidence','method_code')]+[('limitations_jsonb','limitations')])
        for value in evidence:
            links=c.execute(select(table('evidence_ocr_tokens').c.token_id).where(table('evidence_ocr_tokens').c.evidence_id==UUID(value['id'])).order_by(table('evidence_ocr_tokens').c.token_order)).scalars().all()
            if [str(identifier) for identifier in links]!=value['token_ids']:raise OpsError('RESTORE_EVIDENCE_MISMATCH')
        checked_service+=1
    for package in c.execute(select(table('import_packages')).where(table('import_packages').c.import_state=='completed')).mappings():
        provenance=package['provenance_jsonb'] or {};validation=package['local_validation_jsonb'] or {}
        if 'envelope' not in provenance:raise OpsError('IMPORT_PROVENANCE_UNAVAILABLE')
        if digest('import_package',provenance['envelope'],'cc-import-envelope-v1')!=package['package_fingerprint']:raise OpsError('RESTORE_IMPORT_HASH_MISMATCH')
        ids=provenance['resources'];version=one(c,'criteria_versions','criteria_version_id',UUID(ids['criteria_version_id']));run=one(c,'review_runs','run_id',UUID(ids['run_id']))
        if digest('criteria_import',validation['criteria_payload'],'cc-import-projection-v1')!=version['criteria_hash'] or digest('review_import',validation['result_payload'],'cc-import-projection-v1')!=run['result_manifest_hash']:raise OpsError('RESTORE_IMPORT_HASH_MISMATCH')
        with storage.read('intake/'+provenance['operation_id']+'/plan.json') as stream:plan_raw=stream.read(20*1024*1024+1)
        if hashlib.sha256(plan_raw).hexdigest()!=provenance['plan_sha256']:raise OpsError('RESTORE_IMPORT_PLAN_MISMATCH')
        plan=strict_json(plan_raw)
        if plan['resources']!=ids or plan['envelope']!=provenance['envelope']:raise OpsError('RESTORE_IMPORT_PLAN_MISMATCH')
        rows=c.execute(select(table('criteria_items')).where(table('criteria_items').c.criteria_version_id==version['criteria_version_id']).order_by(table('criteria_items').c.position)).mappings().all()
        fields=projection_fields(provenance['envelope']['projection_profile']['version'])
        if [{key:row[key] for key in fields} for row in rows]!=validation['criteria_payload']['items']:raise OpsError('RESTORE_IMPORT_GRAPH_MISMATCH')
        actual_results=c.execute(select(table('item_results')).where(table('item_results').c.run_id==run['run_id'])).mappings().all()
        if {str(row['result_id']) for row in actual_results}!=set(ids['results'].values()):raise OpsError('RESTORE_IMPORT_GRAPH_MISMATCH')
        if (str(run['run_id'])!=validation['result_payload']['run_id'] or str(run['selected_attempt_id'])!=validation['result_payload']['imported_attempt_id']
            or run['overall_verdict'] is not None or version['confirmed_by'] is not None):raise OpsError('RESTORE_IMPORT_GRAPH_MISMATCH')
        for item in provenance['envelope']['source_files']:
            with storage.read('intake/'+provenance['operation_id']+'/sources/'+item['relative_path']) as stream:verify_bytes(stream.read(int(item['byte_size'])+1),item)
        checked_import+=1
    return dict(schema=schema,all_foreign_keys_checked=True,all_roots_and_lineage_checked=True,service_manifests_checked=checked_service,import_manifests_checked=checked_import)


class Backups:
    def __init__(self,engine,settings,tools):self.engine,self.settings,self.tools=engine,settings,tools

    def capture(self,storage,target,app_values,*,drain_seconds=900,legacy_source=None):
        app_settings,keys=secret_hashes(app_values)
        if (app_settings.database.database!=self.settings.database or app_settings.storage_root!=storage.path
            or app_settings.database.host!=self.settings.host or app_settings.database.port!=self.settings.port
            or app_settings.database.username!=self.settings.app_user):raise OpsError('BACKUP_APP_TARGET_MISMATCH')
        try:
            with storage.read('restore-hold.json'):pass
        except FileNotFoundError:pass
        else:raise OpsError('RESTORE_VALIDATION_REQUIRED')
        target=Path(target)
        if target==storage.path or storage.path in target.parents:raise OpsError('BACKUP_OUTSIDE_STORAGE_REQUIRED')
        started=time.perf_counter();backup_id=str(uuid4())
        with Tree(target,create=True,private=True) as out:
            out.write('INCOMPLETE.json',json_bytes({'backup_id':backup_id,'state':'incomplete'}))
            with offline(self.engine,storage,drain_seconds=drain_seconds),frozen_database(self.engine) as frozen:
                versions={name:self.tools.version(name) for name in ('pg_dump','pg_restore')}
                checks=graph_check(frozen,storage,self.settings.app_user);before=snapshot(frozen)
                files=frozen.execute(select(table('stored_files'))).mappings().all();file_rows=[];degraded=False
                for row in files:
                    try:
                        with storage.verified(row['storage_key'],row['byte_size'],row['sha256']):pass
                        available=True
                    except Exception:available=False
                    file_rows.append(dict(file_id=str(row['file_id']),storage_key=row['storage_key'],state=row['state'],byte_size=str(row['byte_size']),sha256=row['sha256'],available=available))
                    degraded=degraded or not available or row['state']=='missing'
                with Tree(storage.path,private=True) as source:
                    inventory=source.inventory(max_files=MAX_BACKUP_FILES,max_bytes=MAX_BACKUP_BYTES,max_path_bytes=1024)
                    # OS lock files are process state, never restored as proof of a live process.
                    inventory=[item for item in inventory if not item['relative_path'].startswith('locks/')]
                    for item in inventory:
                        with source.read(item['relative_path']) as stream:out.copy_from('storage/'+item['relative_path'],stream,item)
                with out.directory() as directory:fd=os.open('database.dump',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
                with os.fdopen(fd,'wb') as stream:self.tools.run('pg_dump',['--format=custom','--no-tablespaces',*self.tools.connection()],stdout=stream);stream.flush();os.fsync(stream.fileno())
                with out.read('database.dump') as stream:dump_info=dict(relative_path='database.dump',byte_size=str(os.fstat(stream.fileno()).st_size),sha256=hashlib.file_digest(stream,'sha256').hexdigest())
                if snapshot(frozen)!=before:raise OpsError('BACKUP_DATABASE_CHANGED')
                validate_schema(frozen,app_user=self.settings.app_user)
                with Tree(storage.path,private=True) as source:
                    after=[item for item in source.inventory(max_files=MAX_BACKUP_FILES,max_bytes=MAX_BACKUP_BYTES,max_path_bytes=1024) if not item['relative_path'].startswith('locks/')]
                if after!=inventory:raise OpsError('BACKUP_FILES_CHANGED')
                legacy=[]
                if legacy_source is not None:
                    with Tree(Path(legacy_source)) as source:
                        legacy=source.inventory()
                        for item in legacy:
                            with source.read(item['relative_path']) as stream:out.copy_from('legacy/'+item['relative_path'],stream,item)
                        if source.inventory()!=legacy:raise OpsError('LEGACY_SNAPSHOT_CHANGED')
                pending={name:frozen.exec_driver_sql(sql).scalar_one() for name,sql in {
                    'queued_runs':"SELECT count(*) FROM public.review_runs WHERE execution_status='queued'",
                    'unfinished_packages':"SELECT count(*) FROM public.import_packages WHERE import_state<>'completed'"}.items()}
                manifest=dict(contract_version='cc-backup-set-v1',backup_id=backup_id,state='degraded' if degraded else 'complete',restore_ready=False,
                    captured_at=datetime.now(timezone.utc).isoformat(),schema_revision='M008',application_contract='cc-operations-v1',
                    source=dict(database=self.settings.database,owner_role=self.settings.owner_user,app_role=self.settings.app_user),
                    postgresql_version=frozen.exec_driver_sql('SHOW server_version_num').scalar_one(),tools=versions,dump=dump_info,
                    storage_files=inventory,registered_files=file_rows,tables=before,pending=pending,checks=checks,key_fingerprints=keys,
                    intake_included=True,legacy_included=legacy_source is not None,legacy_files=legacy,
                    capture_seconds=round(time.perf_counter()-started,4),limits=dict(files=MAX_BACKUP_FILES,bytes=str(MAX_BACKUP_BYTES)))
                raw=json_bytes(manifest);out.write('manifest.json',raw)
                out.write('COMPLETE.json',json_bytes(dict(contract_version='cc-backup-complete-v1',manifest_sha256=hashlib.sha256(raw).hexdigest(),state=manifest['state'])))
        return manifest

    def inspect(self,target):
        with Tree(target,private=True) as source:
            raw=source.bytes('manifest.json',20*1024*1024);manifest=strict_json(raw);complete=strict_json(source.bytes('COMPLETE.json',262144))
            if complete['manifest_sha256']!=hashlib.sha256(raw).hexdigest() or manifest['contract_version']!='cc-backup-set-v1' or manifest['state']!='complete':raise OpsError('BACKUP_NOT_RESTORE_READY')
            for item in [manifest['dump'],*[dict(value,relative_path='storage/'+value['relative_path']) for value in manifest['storage_files']],*[dict(value,relative_path='legacy/'+value['relative_path']) for value in manifest['legacy_files']]]:
                with source.read(item['relative_path']) as stream:
                    if os.fstat(stream.fileno()).st_size!=int(item['byte_size']) or hashlib.file_digest(stream,'sha256').hexdigest()!=item['sha256']:raise OpsError('BACKUP_BYTES_MISMATCH')
            drills=0
            try:
                with source.directory('verifications') as fd:names=os.listdir(fd)
            except FileNotFoundError:names=[]
            for name in names:
                if not re.fullmatch(r'[0-9a-f]{32}\.json',name):raise OpsError('BACKUP_VERIFICATION_INVALID')
                record=strict_json(source.bytes('verifications/'+name,262144));report=record.get('report',{})
                if (record.get('manifest_sha256')!=complete['manifest_sha256'] or report.get('backup_id')!=manifest['backup_id']
                    or report.get('state')!='validated_held' or report.get('restore_ready') is not True
                    or report.get('original_table_rows_and_hashes_matched') is not True or report.get('security_keys_rotated') is not True):raise OpsError('BACKUP_VERIFICATION_INVALID')
                drills+=1
            manifest.update(restore_ready=bool(drills),verified_restore_drills=drills)
        return manifest

    def restore(self,backup,new_root,app_values):
        started=time.perf_counter();manifest=self.inspect(backup);new_root=Path(new_root)
        if Path(backup)==new_root or Path(backup) in new_root.parents:raise OpsError('SEPARATE_RESTORE_ROOT_REQUIRED')
        if self.settings.database==manifest['source']['database'] or self.settings.owner_user!=manifest['source']['owner_role'] or self.settings.app_user!=manifest['source']['app_role']:raise OpsError('FRESH_DATABASE_SAME_ROLES_REQUIRED')
        app_settings,keys=secret_hashes(app_values)
        if (app_settings.database.database!=self.settings.database or app_settings.storage_root!=new_root
            or app_settings.database.host!=self.settings.host or app_settings.database.port!=self.settings.port
            or app_settings.database.username!=self.settings.app_user):raise OpsError('RESTORE_APP_TARGET_MISMATCH')
        if any(keys[key]==manifest['key_fingerprints'][key] for key in keys):raise OpsError('RESTORE_FRESH_SECURITY_KEYS_REQUIRED')
        with unit_of_work(self.engine) as c:
            if c.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p','v','m','S','f') AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%')").scalar_one():raise OpsError('RESTORE_DATABASE_NOT_EMPTY')
        # New root remains fail-closed even if pg_restore or later validation fails.
        LocalStorage.initialize(new_root)
        storage=LocalStorage(new_root)
        try:
            storage.write_new('restore-hold.json',json_bytes({'backup_id':manifest['backup_id'],'validation':'pending'}))
            self.tools.version('pg_restore')
            with Tree(backup,private=True) as source:
                with source.read('database.dump') as stream:self.tools.run('pg_restore',['--single-transaction','--exit-on-error',*self.tools.connection()],stdin=stream)
                with Tree(new_root,private=True) as destination:
                    for item in manifest['storage_files']:
                        name=item['relative_path']
                        if name in ('marker','restore-hold.json') or name.startswith('locks/'):continue
                        if name=='restore-report.json':name='recovery-history/'+manifest['backup_id']+'/restore-report.json'
                        with source.read('storage/'+item['relative_path']) as stream:destination.copy_from(name,stream,item)
                    for item in manifest['legacy_files']:
                        with source.read('legacy/'+item['relative_path']) as stream:destination.copy_from('legacy-snapshot/'+item['relative_path'],stream,item)
            with unit_of_work(self.engine) as c:
                before=snapshot(c)
                if before!=manifest['tables']:raise OpsError('RESTORE_TABLE_DATA_MISMATCH')
                # A single-database archive without --create does not carry database
                # ACLs. Reapply exactly M008's reviewed boundary on this NEW target.
                quote=c.dialect.identifier_preparer.quote
                db,app=quote(self.settings.database),quote(self.settings.app_user)
                c.exec_driver_sql('REVOKE CREATE, TEMPORARY ON DATABASE '+db+' FROM '+app+', PUBLIC')
                c.exec_driver_sql('GRANT CONNECT ON DATABASE '+db+' TO '+app)
                checks_recreated=restore_check_rendering(c,self.settings.app_user)
                checks=graph_check(c,storage,self.settings.app_user)
                for item in manifest['registered_files']:
                    with storage.verified(item['storage_key'],int(item['byte_size']),item['sha256']):pass
                sessions=table('user_sessions')
                revoked=c.execute(update(sessions).where(sessions.c.revoked_at.is_(None)).values(revoked_at=func.clock_timestamp())).rowcount
                # One per project: schema requires every audit event to have a real project.
                for pid in c.execute(select(table('projects').c.project_id)).scalars():audit(c,pid,'restore.sessions_revoked','backup_restore',UUID(manifest['backup_id']))
            report=dict(contract_version='cc-restore-report-v1',backup_id=manifest['backup_id'],database=self.settings.database,
                state='validated_held',restore_ready=True,checks=checks,original_table_rows_and_hashes_matched=True,sessions_revoked=revoked,
                security_keys_rotated=True,key_fingerprints=keys,pending=manifest['pending'],worker_started=False,api_started=False,
                reviewed_checks_recreated_and_validated=checks_recreated,
                restore_seconds=round(time.perf_counter()-started,4),backup_capture_seconds=manifest['capture_seconds'],rpo_target_verified=False,rto_target_verified=False)
            storage.write_new('restore-report.json',json_bytes(report))
            with Tree(backup,private=True) as source:
                source.write('verifications/'+uuid4().hex+'.json',json_bytes(dict(manifest_sha256=hashlib.sha256(source.bytes('manifest.json',20*1024*1024)).hexdigest(),report=report)))
            return report
        finally:storage.close()

    def release(self,storage,app_values):
        settings,keys=secret_hashes(app_values)
        with storage.read('restore-report.json') as stream:report=strict_json(stream.read(262145))
        if (report['state']!='validated_held' or report['database']!=self.settings.database or settings.storage_root!=storage.path
            or settings.database.database!=self.settings.database or settings.database.host!=self.settings.host
            or settings.database.port!=self.settings.port or settings.database.username!=self.settings.app_user
            or keys!=report['key_fingerprints'] or not report['security_keys_rotated']):raise OpsError('RESTORE_VALIDATION_REQUIRED')
        with offline(self.engine,storage,drain_seconds=0),unit_of_work(self.engine) as c:
            graph_check(c,storage,self.settings.app_user)
            for row in c.execute(select(table('stored_files'))).mappings():
                if row['state']!='stored':raise OpsError('RESTORE_FILE_UNAVAILABLE')
                with storage.verified(row['storage_key'],row['byte_size'],row['sha256']):pass
            # A fresh restore starts with no active session; new login is an explicit later action.
            if c.exec_driver_sql('SELECT EXISTS(SELECT 1 FROM public.user_sessions WHERE revoked_at IS NULL)').scalar_one():raise OpsError('RESTORE_ACTIVE_SESSION_REJECTED')
            if settings.secure and c.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM public.users WHERE account_kind='developer' AND is_active)").scalar_one():raise OpsError('RESTORE_DEVELOPER_ACTIVE')
            for pid in c.execute(select(table('projects').c.project_id)).scalars():audit(c,pid,'restore.released','backup_restore',UUID(report['backup_id']))
        with storage.directory() as fd:os.unlink('restore-hold.json',dir_fd=fd);os.fsync(fd)
        return dict(state='released',worker_started=False,api_started=False,pending_requires_explicit_start=report['pending'])
