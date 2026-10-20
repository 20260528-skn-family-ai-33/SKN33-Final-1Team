"""Frozen one-poster import. Source facts stay unconfirmed and source verdicts stay null locally."""
from pathlib import Path
import hashlib,os
from uuid import UUID,uuid4
from sqlalchemy import select,insert,update,func
from ..hashing.canonical import digest
from ..persistence.transaction import unit_of_work
from ..persistence.database import PersistenceError
from ..services.business import table,one
from ..storage.images import normalize
from ..storage.local import key,operation_key
from ..storage.local import StorageError
from .io import OpsError,source_tree,strict_json,json_bytes,verify_bytes,relative
from .maintenance import MAINTENANCE_KEY,session_lock
from .files import audit

PROFILE='conservative-source-v1'
ENVELOPE='cc-import-envelope-v1'
PROJECTION='cc-import-projection-v1'


def owner(c,pid,actor):
    project=one(c,'projects','project_id',pid)
    user=one(c,'users','user_id',actor)
    member=c.execute(select(table('project_members')).where(table('project_members').c.project_id==pid,table('project_members').c.user_id==actor)).mappings().one_or_none()
    if not user['is_active'] or not member or not member['is_active'] or member['role']!='owner':raise OpsError('IMPORT_OWNER_REQUIRED')
    if project['business_state']!='active':raise OpsError('PROJECT_ARCHIVED')
    return project


def identity(value):
    if type(value) not in (int,str) or isinstance(value,str) and not value.strip():raise OpsError('SOURCE_ID_UNSUPPORTED')
    result=str(value)
    if len(result)>200:raise OpsError('SOURCE_ID_UNSUPPORTED')
    return result


class Imports:
    def __init__(self,engine,storage):self.engine,self.storage=engine,storage

    def projection(self,read,inventory,profile):
        names={item['relative_path'] for item in inventory};reasons=[];parsed={}
        for item in inventory:
            name=item['relative_path']
            if name.endswith('.json'):
                try:parsed[name]=strict_json(read(name,20*1024*1024))
                except OpsError:reasons.append('SOURCE_JSON_UNSUPPORTED')
        paths=dict(snapshot='snapshot.json',result='result.json',image='original.bin' if 'original.bin' in names else 'normalized.png')
        reported=None;external=False
        if 'manifest.json' in parsed:
            manifest=parsed['manifest.json'];external=True
            if not isinstance(manifest,dict) or manifest.get('contract_version')!='cc-import-source-v1':reasons.append('SOURCE_MANIFEST_UNSUPPORTED')
            else:
                paths=manifest.get('files',{})
                if not isinstance(paths,dict) or set(paths)!={'snapshot','result','image'}:reasons.append('SOURCE_MANIFEST_UNSUPPORTED');paths={}
                try:
                    for value in paths.values():relative(value)
                except (OpsError,ValueError,TypeError):reasons.append('SOURCE_MANIFEST_UNSUPPORTED');paths={}
                reported=manifest.get('sha256')
                if reported is not None:
                    actual={item['relative_path']:item['sha256'] for item in inventory}
                    if not isinstance(reported,dict) or any(name not in actual or actual[name]!=value for name,value in reported.items()):reasons.append('REPORTED_HASH_MISMATCH')
        snapshot=parsed.get(paths.get('snapshot'));result=parsed.get(paths.get('result'))
        if not isinstance(snapshot,dict) or not isinstance(result,dict):reasons.append('SOURCE_GRAPH_INCOMPLETE');snapshot=snapshot if isinstance(snapshot,dict) else {};result=result if isinstance(result,dict) else {}
        items=snapshot.get('criteria');results=result.get('results',[]);normalized=[];mapped=[]
        try:
            if not isinstance(items,list) or not 1<=len(items)<=100 or not isinstance(results,list) or len(results)>100:raise OpsError('SOURCE_CRITERIA_UNSUPPORTED')
            seen={}
            for position,item in enumerate(items,1):
                if not isinstance(item,dict) or not isinstance(item.get('text'),str) or not item['text'].strip() or len(item['text'])>500:raise OpsError('SOURCE_CRITERIA_UNSUPPORTED')
                external_id=identity(item.get('id'))
                if external_id in seen:raise OpsError('SOURCE_ID_AMBIGUOUS')
                seen[external_id]=type(item['id'])
                normalized.append(dict(external_item_id=external_id,position=position,category=item.get('type') if item.get('type') in ('information','visual') else 'unknown',text=item['text'],
                    source_text=item.get('source') if isinstance(item.get('source'),str) else None,source_item_jsonb=item))
            seen_results=set()
            for value in results:
                if not isinstance(value,dict):raise OpsError('SOURCE_RESULT_UNSUPPORTED')
                original_id=value.get('criterion_id');external_id=identity(original_id)
                if external_id not in seen or seen[external_id]!=type(original_id) or external_id in seen_results:raise OpsError('SOURCE_RESULT_MAPPING_UNCONFIRMED')
                seen_results.add(external_id);mapped.append((external_id,value))
        except OpsError as error:reasons.append(error.code)
        image_path=paths.get('image');display=measurements=None
        try:
            if result.get('id') is not None:identity(result['id'])
        except OpsError as error:reasons.append(error.code)
        if image_path in names:
            try:display,measurements=normalize(read(image_path,20*1024*1024))
            except Exception:reasons.append('SOURCE_IMAGE_UNSUPPORTED')
        elif not profile['assignment']['existing_image_id']:reasons.append('SOURCE_IMAGE_MISSING')
        known_images={'original.bin','normalized.png','ocr_input.png',image_path}
        for name in names&{'normalized.png','ocr_input.png'}:
            try:
                _,metadata=normalize(read(name,20*1024*1024))
                if metadata['actual_format']!='PNG':reasons.append('SOURCE_IMAGE_UNSUPPORTED')
            except Exception:reasons.append('SOURCE_IMAGE_UNSUPPORTED')
        if any(Path(name).suffix.lower() in ('.png','.jpg','.jpeg','.webp') and name not in known_images for name in names):reasons.append('MULTI_POSTER_UNSUPPORTED')
        return dict(state='needs_review' if reasons else 'ready',reasons=sorted(set(reasons)),snapshot=snapshot,result=result,items=normalized,results=mapped,
            paths=paths,image_path=image_path if image_path in names else None,display=display,measurements=measurements,external=external,
            reported_hash='mismatch' if 'REPORTED_HASH_MISMATCH' in reasons else 'matched' if reported is not None else 'not_checked')

    def dry_run(self,source,allowed,*,project_id,actor_id,namespace,display_name,existing_image_id=None,criteria_set_id=None):
        path=Path(source)
        if path==self.storage.path or self.storage.path in path.parents or path in self.storage.path.parents:raise OpsError('INPUT_OVERLAPS_STORAGE')
        if not namespace.strip() or len(namespace)>100 or not display_name.strip() or len(display_name)>200:raise OpsError('INVALID_IMPORT_PROFILE')
        profile=dict(name='one-poster-source',version=PROFILE,display_name=display_name,
            assignment=dict(existing_image_id=str(existing_image_id) if existing_image_id else None,criteria_set_id=str(criteria_set_id) if criteria_set_id else None),
            policies=dict(unknown_requester='preserve_null',verdict='source_only',coordinates='preserve_reported_only',display='new_cc_display_bytes'))
        with source_tree(source,allowed) as tree:
            inventory=tree.inventory()
            projected=self.projection(tree.bytes,inventory,profile)
            with unit_of_work(self.engine) as c:
                c.exec_driver_sql('SET TRANSACTION READ ONLY');owner(c,project_id,actor_id)
                if criteria_set_id:
                    if one(c,'criteria_sets','criteria_set_id',criteria_set_id)['project_id']!=project_id:raise OpsError('IMPORT_BINDING_MISMATCH')
                if existing_image_id:
                    if one(c,'asset_versions','asset_version_id',existing_image_id)['project_id']!=project_id:raise OpsError('IMPORT_BINDING_MISMATCH')
            payload=dict(source_namespace=namespace,target_project_id=str(project_id),source_files=inventory,projection_profile=profile)
            fingerprint=digest('import_package',payload,ENVELOPE);op=str(uuid4())
            resources={name:str(uuid4()) for name in ('import_id','asset_id','image_id','original_frame_id','display_frame_id','criteria_set_id','criteria_version_id','run_id','attempt_id','display_file_id','manifest_file_id')}
            resources['files']={item['relative_path']:str(uuid4()) for item in inventory}
            resources['items']={item['external_item_id']:str(uuid4()) for item in projected['items']}
            resources['results']={identifier:str(uuid4()) for identifier,_ in projected['results']}
            try:source_id=identity(projected['result']['id']) if projected['result'].get('id') is not None else None
            except OpsError:source_id=None
            plan=dict(contract_version='cc-import-plan-v1',operation_id=op,package_fingerprint=fingerprint,envelope=payload,resources=resources,
                external_record_id=source_id,reported_validation={'external_hash':projected['reported_hash']},
                source_inventory_hash=digest('source_inventory',{'source_files':inventory},'cc-source-inventory-v1'),
                state=projected['state'],reasons=projected['reasons'],reported_hash=projected['reported_hash'],
                preview=dict(criteria=len(projected['items']),results=len(projected['results']),results_complete=projected['state']=='ready' and bool(projected['items']) and len(projected['results'])==len(projected['items']),
                             confirmation_state='unconfirmed',execution_status='imported',local_verdict=None,ocr_projection='source_only',external_schema='unavailable'))
            with self.storage.lock('operation-'+op):
                for item in inventory:
                    raw=verify_bytes(tree.bytes(item['relative_path'],512*1024*1024),item)
                    self.storage.write_new('intake/'+op+'/sources/'+item['relative_path'],raw)
                if tree.inventory()!=inventory:raise OpsError('INPUT_CHANGED')
                frozen=json_bytes(plan)
                self.storage.write_new('intake/'+op+'/plan.json',frozen)
                self.storage.write_new('intake/'+op+'/plan.sha256',hashlib.sha256(frozen).hexdigest().encode('ascii'))
        return plan

    def load(self,op):
        op=str(UUID(str(op)))
        with self.storage.read('intake/'+op+'/plan.json') as stream:raw=stream.read(20*1024*1024+1)
        with self.storage.read('intake/'+op+'/plan.sha256') as stream:expected=stream.read(65).decode('ascii')
        if hashlib.sha256(raw).hexdigest()!=expected:raise OpsError('IMPORT_PLAN_CHANGED')
        plan=strict_json(raw)
        if plan['contract_version']!='cc-import-plan-v1' or plan['operation_id']!=op or digest('import_package',plan['envelope'],ENVELOPE)!=plan['package_fingerprint']:
            raise OpsError('IMPORT_PLAN_INVALID')
        plan['_plan_sha256']=expected
        return plan

    def apply(self,op,source,allowed,*,actor_id,revalidate=False):
        plan=self.load(op);pid=UUID(plan['envelope']['target_project_id']);fingerprint=plan['package_fingerprint']
        lock=int.from_bytes(hashlib.sha256(('cc-import:'+str(pid)+fingerprint).encode()).digest()[:8],'big',signed=True)
        with session_lock(self.engine,MAINTENANCE_KEY,shared=True),session_lock(self.engine,lock):
            with source_tree(source,allowed) as tree:
                if tree.inventory()!=plan['envelope']['source_files']:raise OpsError('INPUT_CHANGED')
            with unit_of_work(self.engine) as c:
                owner(c,pid,actor_id);packages=table('import_packages')
                existing=c.execute(select(packages).where(packages.c.project_id==pid,packages.c.source_namespace==plan['envelope']['source_namespace'],packages.c.package_fingerprint==fingerprint)).mappings().one_or_none()
                if existing:
                    if existing['import_state']=='completed':return dict(import_id=str(existing['import_id']),state='completed',replayed=True,resources=existing['provenance_jsonb']['resources'])
                    if not revalidate:raise OpsError('IMPORT_REVALIDATION_REQUIRED')
                    plan=self.load(existing['provenance_jsonb']['operation_id'])
                    if plan['package_fingerprint']!=fingerprint or plan['_plan_sha256']!=existing['provenance_jsonb']['plan_sha256']:raise OpsError('IMPORT_PLAN_CONFLICT')
                    audit(c,pid,'import.revalidate','import_package',existing['import_id'])
                    if existing['import_state']=='failed':c.execute(update(packages).where(packages.c.import_id==existing['import_id']).values(import_state='received'))
                else:
                    c.execute(insert(packages).values(import_id=UUID(plan['resources']['import_id']),project_id=pid,source_namespace=plan['envelope']['source_namespace'],
                        package_fingerprint=fingerprint,fingerprint_version=ENVELOPE,imported_by=actor_id,
                        external_record_id=plan['external_record_id'],reported_validation_jsonb=plan['reported_validation'],
                        provenance_jsonb=dict(operation_id=plan['operation_id'],plan_sha256=plan['_plan_sha256'],resources=plan['resources'],envelope=plan['envelope'],source_inventory_hash=plan['source_inventory_hash'])))
                    audit(c,pid,'import.received','import_package',UUID(plan['resources']['import_id']))
            op=plan['operation_id']
            def check_source():
                with source_tree(source,allowed) as tree:
                    if tree.inventory()!=plan['envelope']['source_files']:raise OpsError('INPUT_CHANGED')
            with self.storage.lock('operation-'+op):
                def read(name,limit):
                    item=next(value for value in plan['envelope']['source_files'] if value['relative_path']==name)
                    with self.storage.read('intake/'+op+'/sources/'+name) as stream:raw=stream.read(limit+1)
                    if len(raw)>limit:raise OpsError('INPUT_LIMIT_EXCEEDED')
                    return verify_bytes(raw,item)
                # Always validate every retained source, including unprojected diagnostics.
                for item in plan['envelope']['source_files']:read(item['relative_path'],512*1024*1024)
                projected=self.projection(read,plan['envelope']['source_files'],plan['envelope']['projection_profile'])
                if projected['state']!=plan['state'] or projected['reasons']!=plan['reasons']:raise OpsError('IMPORT_PLAN_CONFLICT')
                if plan['state']!='ready':
                    check_source()
                    with unit_of_work(self.engine) as c:
                        owner(c,pid,actor_id)
                        c.execute(update(table('import_packages')).where(table('import_packages').c.import_id==UUID(plan['resources']['import_id'])).values(
                            import_state='needs_review',hash_check_state='mismatch' if projected['reported_hash']=='mismatch' else 'matched',
                            local_validation_jsonb=dict(reasons=plan['reasons'],external_schema='unavailable',external_hash=projected['reported_hash'],intake_verified=True)))
                        audit(c,pid,'import.needs_review','import_package',UUID(plan['resources']['import_id']))
                    return dict(import_id=plan['resources']['import_id'],state='needs_review',replayed=False,reasons=plan['reasons'])
                try:return self.complete(plan,projected,read,actor_id,check_source)
                except PersistenceError as error:
                    if error.outcome in ('commit_unknown','committed','rollback_unconfirmed'):
                        with unit_of_work(self.engine) as c:
                            if c.exec_driver_sql('SELECT pg_is_in_recovery()').scalar_one():raise OpsError('IMPORT_OUTCOME_UNCONFIRMED')
                            if not c.exec_driver_sql('SELECT pg_try_advisory_xact_lock(%s)',(operation_key(op),)).scalar_one():raise OpsError('IMPORT_OUTCOME_UNCONFIRMED')
                            package=one(c,'import_packages','import_id',UUID(plan['resources']['import_id']))
                            if package['import_state']=='completed':return dict(import_id=plan['resources']['import_id'],state='completed',replayed=True,resources=plan['resources'])
                        raise OpsError('IMPORT_OUTCOME_UNCONFIRMED') from None
                    with unit_of_work(self.engine) as c:
                        c.execute(update(table('import_packages')).where(table('import_packages').c.import_id==UUID(plan['resources']['import_id'])).values(import_state='failed',local_validation_jsonb={'code':'IMPORT_GRAPH_ROLLED_BACK'}))
                        audit(c,pid,'import.failed','import_package',UUID(plan['resources']['import_id']))
                    raise

    def complete(self,plan,source,read,actor,check_source=None):
        pid=UUID(plan['envelope']['target_project_id']);ids=plan['resources'];op=plan['operation_id'];package_id=UUID(ids['import_id'])
        profile=plan['envelope']['projection_profile'];binding=profile['assignment'];files=[]
        def prepare(file_id,role,raw,mime='application/json',name=None):
            value=dict(file_id=file_id,project_id=str(pid),file_role=role,storage_key=key(pid,file_id),byte_size=str(len(raw)),sha256=hashlib.sha256(raw).hexdigest(),mime=mime,relative_path=name)
            path='staging/'+op+'/'+file_id
            try:
                with self.storage.verified(value['storage_key'],len(raw),value['sha256']):pass
            except (FileNotFoundError,StorageError):pass
            else:files.append(value);return value
            try:self.storage.write_new(path,raw)
            except FileExistsError:
                try:
                    with self.storage.read(path) as stream:verify_bytes(stream.read(len(raw)+1),value)
                except StorageError:
                    with self.storage.read(path,links=2) as stream:verify_bytes(stream.read(len(raw)+1),value)
            files.append(value);return value
        for item in plan['envelope']['source_files']:
            name=item['relative_path']
            role='import_manifest' if name=='manifest.json' else 'source_json'
            mime='application/json'
            if name==source['image_path']:role='original_image';mime='image/'+source['measurements']['actual_format'].lower()
            elif name in ('normalized.png','ocr_input.png'):role='display_image' if name=='normalized.png' else 'ocr_input';mime='image/png'
            elif not name.endswith('.json'):continue  # Retained in immutable intake and backup; never mislabeled as JSON.
            prepare(ids['files'][name],role,read(name,512*1024*1024),mime,name)
        if not binding['existing_image_id']:
            prepare(ids['display_file_id'],'display_image',source['display'],'image/png')
        if not source['external']:
            prepare(ids['manifest_file_id'],'import_manifest',json_bytes(dict(contract_version='cc-local-import-receipt-v1',generated_locally=True,original_manifest_present=False,package_fingerprint=plan['package_fingerprint'],source_files=plan['envelope']['source_files'])))
        manifest_file=ids['files']['manifest.json'] if source['external'] else ids['manifest_file_id']
        image_id=UUID(binding['existing_image_id'] or ids['image_id']);version_id=UUID(ids['criteria_version_id']);run_id=UUID(ids['run_id']);attempt_id=UUID(ids['attempt_id'])
        intent=dict(contract_version='cc-storage-journal-v1',operation='import_package',operation_id=op,project_id=str(pid),image_id=str(image_id),
            import_id=ids['import_id'],files=[{k:v for k,v in value.items() if k not in ('mime','relative_path')} for value in files],phase='staged',db_outcome='not_started')
        self.storage.journal(intent)
        if check_source:check_source()
        with self.storage.lock('gate',shared=True):
            for value in files:
                try:
                    with self.storage.verified(value['storage_key'],int(value['byte_size']),value['sha256']):pass
                except (FileNotFoundError,StorageError):self.storage.publish(op,value)
            intent.update(phase='finalized',db_outcome='unconfirmed');self.storage.journal(intent)
            with unit_of_work(self.engine,import_operation=True) as c:
                c.exec_driver_sql('SELECT pg_advisory_xact_lock(%s)',(operation_key(op),))
                c.exec_driver_sql('SELECT public.cc_lock_project(%s)',(pid,));owner(c,pid,actor)
                package=one(c,'import_packages','import_id',package_id,lock=True)
                if package['import_state'] not in ('received','needs_review'):raise OpsError('IMPORT_STATE_CHANGED')
                for value in files:
                    c.execute(insert(table('stored_files')).values(file_id=UUID(value['file_id']),project_id=pid,import_package_id=package_id,
                        file_role=value['file_role'],storage_key=value['storage_key'],byte_size=int(value['byte_size']),sha256=value['sha256'],detected_mime=value['mime'],
                        parent_file_id=UUID(ids['files'][source['image_path']]) if value['file_id']==ids['display_file_id'] and not binding['existing_image_id'] else None,
                        created_by=actor,source_metadata=dict(operation_id=op,relative_path=value['relative_path'],origin='import_source')))
                if not binding['existing_image_id']:
                    measurement=source['measurements'];original=next(item for item in files if item['relative_path']==source['image_path'])
                    c.execute(insert(table('assets')).values(asset_id=UUID(ids['asset_id']),project_id=pid,name=profile['display_name'],created_by=actor))
                    c.execute(insert(table('asset_versions')).values(asset_version_id=image_id,project_id=pid,asset_id=UUID(ids['asset_id']),version_no=1,
                        original_file_id=UUID(original['file_id']),display_file_id=UUID(ids['display_file_id']),original_sha256=original['sha256'],
                        **{name:measurement[name] for name in ('original_width','original_height','display_width','display_height')},measurement_metadata=measurement,created_by=actor))
                    for kind in ('original','display'):
                        c.execute(insert(table('image_coordinate_frames')).values(frame_id=UUID(ids[kind+'_frame_id']),project_id=pid,asset_version_id=image_id,
                            file_id=UUID(original['file_id'] if kind=='original' else ids['display_file_id']),parent_frame_id=UUID(ids['original_frame_id']) if kind=='display' else None,
                            frame_kind=kind,coordinate_space='original_pixels' if kind=='original' else 'normalized_original_pixels',width=measurement[kind+'_width'],height=measurement[kind+'_height'],
                            transform_jsonb={'conversion_version':measurement['conversion_version'],'exif_orientation':measurement['exif_orientation']} if kind=='display' else None))
                elif one(c,'asset_versions','asset_version_id',image_id)['project_id']!=pid:raise OpsError('IMPORT_BINDING_MISMATCH')
                set_id=UUID(binding['criteria_set_id'] or ids['criteria_set_id'])
                if not binding['criteria_set_id']:c.execute(insert(table('criteria_sets')).values(criteria_set_id=set_id,project_id=pid,name=profile['display_name'],created_by=actor))
                elif one(c,'criteria_sets','criteria_set_id',set_id)['project_id']!=pid:raise OpsError('IMPORT_BINDING_MISMATCH')
                versions=table('criteria_versions');number=c.execute(select(func.coalesce(func.max(versions.c.version_no),0)).where(versions.c.project_id==pid)).scalar_one()+1
                original_version=source['snapshot'].get('criteria_version')
                criteria_payload=dict(project_id=str(pid),criteria_set_id=str(set_id),image_id=str(image_id),package_fingerprint=plan['package_fingerprint'],
                    source_file_sha256=next(item['sha256'] for item in files if item['relative_path']==source['paths']['snapshot']),source_pointer='/criteria',source_version=original_version,items=source['items'])
                c.execute(insert(versions).values(criteria_version_id=version_id,project_id=pid,criteria_set_id=set_id,target_asset_version_id=image_id,import_package_id=package_id,
                    version_no=number,criteria_hash=digest('criteria_import',criteria_payload,PROJECTION),hash_version=PROJECTION,
                    origin_kind='legacy_snapshot' if not source['external'] and original_version is not None else 'imported',confirmation_state='unconfirmed',
                    source_version_jsonb=original_version,source_snapshot_jsonb=source['snapshot'],source_file_id=UUID(ids['files'][source['paths']['snapshot']])))
                for item in source['items']:
                    c.execute(insert(table('criteria_items')).values(criteria_item_id=UUID(ids['items'][item['external_item_id']]),project_id=pid,target_asset_version_id=image_id,criteria_version_id=version_id,**item))
                c.execute(update(versions).where(versions.c.criteria_version_id==version_id).values(sealed_at=func.clock_timestamp()))
                origin='imported_result' if source['external'] else 'legacy_result'
                c.execute(insert(table('review_runs')).values(run_id=run_id,project_id=pid,asset_version_id=image_id,criteria_version_id=version_id,origin_kind=origin,
                    import_package_id=package_id,source_file_id=UUID(ids['files'][source['paths']['result']]),source_snapshot_jsonb=source['result'],
                    source_status=source['result'].get('status') if isinstance(source['result'].get('status'),str) else None,
                    project_name_snapshot=source['snapshot'].get('project_name') if isinstance(source['snapshot'].get('project_name'),str) else None,
                    execution_status='imported',executor_kind='imported',orchestration_mode='imported',manifest_hash_version=PROJECTION))
                c.execute(insert(table('review_attempts')).values(attempt_id=attempt_id,project_id=pid,asset_version_id=image_id,run_id=run_id,attempt_no=1,execution_status='imported',
                    source_attempt_jsonb={'meaning':'local_import_group_only','original_attempt_number':None}))
                for identifier,value in source['results']:
                    item_id=UUID(ids['items'][identifier]);result_id=UUID(ids['results'][identifier])
                    c.execute(insert(table('item_results')).values(result_id=result_id,project_id=pid,asset_version_id=image_id,run_id=run_id,attempt_id=attempt_id,
                        criteria_version_id=version_id,criteria_item_id=item_id,result_origin=origin,execution_status='imported',verdict=None,source_result_jsonb=value,
                        source_status=value.get('status') if isinstance(value.get('status'),str) else None,observed_value=value.get('observed_value',value.get('observed_text')),
                        reason=value.get('reason') if isinstance(value.get('reason'),str) else None,limitations_jsonb={'coordinates':'source_only','ocr_projection':'source_only','local_verdict':'not_evaluated'}))
                    c.execute(insert(table('run_item_selections')).values(run_id=run_id,criteria_item_id=item_id,result_id=result_id,project_id=pid,asset_version_id=image_id,
                        criteria_version_id=version_id,selection_kind='import_projection'))
                result_payload=dict(run_id=str(run_id),image_id=str(image_id),criteria_version_id=str(version_id),imported_attempt_id=str(attempt_id),package_fingerprint=plan['package_fingerprint'],
                    source_result_sha256=next(item['sha256'] for item in files if item['relative_path']==source['paths']['result']),result_ids=ids['results'],results_complete=len(source['results'])==len(source['items']))
                c.execute(update(table('review_runs')).where(table('review_runs').c.run_id==run_id).values(selected_attempt_id=attempt_id,result_manifest_hash=digest('review_import',result_payload,PROJECTION),sealed_at=func.clock_timestamp(),updated_at=func.clock_timestamp()))
                external_id=source['result'].get('id')
                if external_id is not None:
                    c.execute(insert(table('record_aliases')).values(alias_id=uuid4(),project_id=pid,asset_version_id=image_id,import_package_id=package_id,
                        namespace=plan['envelope']['source_namespace'],external_record_id=identity(external_id)))
                c.execute(update(table('import_packages')).where(table('import_packages').c.import_id==package_id).values(import_state='completed',hash_check_state='matched',manifest_file_id=UUID(manifest_file),
                    local_validation_jsonb=dict(syntax='checked',inventory='matched',external_schema='unavailable',projection_profile=PROFILE,ocr='source_only',criteria_payload=criteria_payload,result_payload=result_payload)))
                audit(c,pid,'import.completed','import_package',package_id)
            intent['db_outcome']='committed';self.storage.journal(intent)
        return dict(import_id=ids['import_id'],state='completed',replayed=False,resources=ids)
