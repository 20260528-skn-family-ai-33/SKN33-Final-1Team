"""Conservative GC and exact-byte repair. Registered files never enter automatic GC."""
import hashlib,os,time
from uuid import UUID,uuid4
from sqlalchemy import select,update,insert,or_,func
from ..persistence.transaction import unit_of_work
from ..services.business import table,one
from ..storage.local import key,operation_key,StorageError
from .io import OpsError,json_bytes,strict_json
from .maintenance import MAINTENANCE_KEY,session_lock


def audit(c,pid,action,target,identifier):
    c.execute(insert(table('audit_events')).values(event_id=uuid4(),project_id=pid,actor_kind='system',action=action,
        target_type=target,target_id=identifier,event_metadata={'management_profile':'cc-operations-v1'}))


def move(storage,source,target):
    """Same-volume, no-clobber move; resume only the exact interrupted link."""
    sp,_,sn=source.rpartition('/');tp,_,tn=target.rpartition('/')
    with storage.directory(sp) as src,storage.directory(tp,create=True) as dst:
        try:os.link(sn,tn,src_dir_fd=src,dst_dir_fd=dst,follow_symlinks=False)
        except FileExistsError:
            a=os.stat(sn,dir_fd=src,follow_symlinks=False);b=os.stat(tn,dir_fd=dst,follow_symlinks=False)
            if (a.st_dev,a.st_ino)!=(b.st_dev,b.st_ino):raise OpsError('QUARANTINE_COLLISION') from None
        os.fsync(dst);os.unlink(sn,dir_fd=src);os.fsync(src)


class FileOperations:
    def __init__(self,engine,storage):self.engine,self.storage=engine,storage

    def unreferenced(self,c,intent):
        if c.exec_driver_sql('SELECT pg_is_in_recovery()').scalar_one():raise OpsError('PRIMARY_REQUIRED')
        if not c.exec_driver_sql('SELECT pg_try_advisory_xact_lock(%s)',(operation_key(intent['operation_id']),)).scalar_one():return 'busy_database'
        files=table('stored_files')
        identifiers=[UUID(item['file_id']) for item in intent['files']];keys=[item['storage_key'] for item in intent['files']]
        if c.execute(select(files.c.file_id).where(or_(files.c.file_id.in_(identifiers),files.c.storage_key.in_(keys))).limit(1)).first():return 'keep_registered'
        if intent['operation']=='upload_image':
            for name,column,value in [('assets','asset_id',intent['asset_id']),('asset_versions','asset_version_id',intent['image_id']),('audit_events','event_id',intent['operation_id'])]:
                if c.execute(select(table(name).c[column]).where(table(name).c[column]==UUID(value))).first():return 'preserve_resource'
        elif intent['operation']=='review_result':
            for name,column,value in [('ocr_runs','ocr_run_id',intent['ocr_id']),('poster_extractions','extraction_id',intent['extraction_id'])]:
                if c.execute(select(table(name).c[column]).where(table(name).c[column]==UUID(value))).first():return 'preserve_resource'
            attempts=table('review_attempts')
            results=table('item_results')
            if c.execute(select(results.c.result_id).where(results.c.attempt_id==UUID(intent['attempt_id']))).first():return 'preserve_resource'
            if c.execute(select(attempts.c.attempt_id).where(attempts.c.attempt_id==UUID(intent['attempt_id']),attempts.c.execution_status=='active')).first():return 'preserve_active_attempt'
        elif intent['operation']=='import_package':
            packages=table('import_packages')
            if c.execute(select(packages.c.import_id).where(packages.c.import_id==UUID(intent['import_id']))).first():return 'preserve_import'
        else:return 'preserve_unknown'
        return 'unreferenced'

    def checked(self,path,item):
        with self.storage.read(path) as stream:
            if os.fstat(stream.fileno()).st_size!=int(item['byte_size']) or hashlib.file_digest(stream,'sha256').hexdigest()!=item['sha256']:
                raise OpsError('GC_BYTES_UNCONFIRMED')

    def collect(self,*,apply=False,delete=False,now=None):
        now=time.time() if now is None else now;report=[]
        with session_lock(self.engine,MAINTENANCE_KEY,shared=True),self.storage.lock('gate'):
            intents={intent['operation_id']:intent for intent in self.storage.journals()}
            for op,intent in intents.items():
                with self.storage.recovery_lock('operation-'+op,concurrent=True) as acquired:
                    if not acquired:report.append(dict(operation_id=op,decision='busy_operation'));continue
                    with unit_of_work(self.engine) as c:
                        decision=self.unreferenced(c,intent)
                        if decision!='unreferenced':report.append(dict(operation_id=op,decision=decision));continue
                        q='quarantine/'+op
                        try:
                            with self.storage.read(q+'/record.json') as stream:record=strict_json(stream.read(262145))
                        except FileNotFoundError:record=None
                        if record is not None:
                            stable=lambda value:{k:v for k,v in value.items() if k not in ('phase','db_outcome')}
                            if stable(record['intent'])!=stable(intent):raise OpsError('GC_JOURNAL_CONFLICT')
                            try:
                                with self.storage.read(q+'/complete.json') as stream:stamp=os.fstat(stream.fileno()).st_mtime
                            except FileNotFoundError:stamp=None
                            if stamp is not None:
                                try:
                                    with self.storage.read(q+'/deleted.json'):pass
                                except FileNotFoundError:pass
                                else:report.append(dict(operation_id=op,decision='already_deleted'));continue
                                decision='eligible_for_delete' if now-stamp>=7*86400 else 'defer_quarantine_grace'
                                if decision=='eligible_for_delete':
                                    record_hash=hashlib.sha256(json_bytes(record)).hexdigest()
                                    try:
                                        with self.storage.read(q+'/delete-started.json') as stream:started=strict_json(stream.read(262145))
                                        if started.get('record_sha256')!=record_hash:raise OpsError('GC_JOURNAL_CONFLICT')
                                    except FileNotFoundError:started=None
                                    for value in record['candidates']:
                                        try:self.checked(value['target'],value)
                                        except FileNotFoundError:
                                            if started is None:raise
                                    if apply and delete:
                                        if started is None:self.storage.write_new(q+'/delete-started.json',json_bytes({'record_sha256':record_hash}))
                                        for value in record['candidates']:
                                            parent,_,name=value['target'].rpartition('/')
                                            with self.storage.directory(parent) as fd:
                                                try:os.unlink(name,dir_fd=fd)
                                                except FileNotFoundError:pass  # Only a recorded, rechecked partial deletion.
                                                os.fsync(fd)
                                        self.storage.write_new(q+'/deleted.json',json_bytes({'deleted_at':str(now)}))
                                        audit(c,UUID(intent['project_id']),'gc.deleted','storage_operation',UUID(op));decision='deleted'
                                report.append(dict(operation_id=op,decision=decision));continue
                            candidates=record['candidates']
                        else:
                            candidates=[];latest=0
                            with self.storage.read('operations/'+op+'/journal.json') as stream:latest=os.fstat(stream.fileno()).st_mtime
                            for item in intent['files']:
                                for source in (item['storage_key'],'staging/'+op+'/'+item['file_id']):
                                    try:
                                        self.checked(source,item)
                                        with self.storage.read(source) as stream:latest=max(latest,os.fstat(stream.fileno()).st_mtime)
                                    except FileNotFoundError:continue
                                    candidates.append(dict(item,source=source,target=q+'/'+item['file_id']))
                            if not candidates:report.append(dict(operation_id=op,decision='no_owned_payload'));continue
                            if now-latest<86400:report.append(dict(operation_id=op,decision='defer_grace'));continue
                            # Two publication links, unknown bytes or colliding destinations are never guessed away.
                            if len({v['file_id'] for v in candidates})!=len(candidates):raise OpsError('GC_PUBLICATION_REQUIRES_RECOVERY')
                            if apply:self.storage.write_new(q+'/record.json',json_bytes(dict(intent=intent,candidates=candidates)))
                        if apply:
                            for value in candidates:
                                try:self.checked(value['target'],value)
                                except FileNotFoundError:move(self.storage,value['source'],value['target']);self.checked(value['target'],value)
                                except StorageError:
                                    # A crash between no-clobber link and unlink is
                                    # recoverable only for this exact source/target inode.
                                    with self.storage.read(value['target'],links=2) as target,self.storage.read(value['source'],links=2) as source:
                                        a,b=os.fstat(target.fileno()),os.fstat(source.fileno())
                                        if (a.st_dev,a.st_ino)!=(b.st_dev,b.st_ino) or a.st_size!=int(value['byte_size']) or hashlib.file_digest(target,'sha256').hexdigest()!=value['sha256']:raise OpsError('GC_PUBLICATION_REQUIRES_RECOVERY')
                                    move(self.storage,value['source'],value['target']);self.checked(value['target'],value)
                            self.storage.write_new(q+'/complete.json',json_bytes({'quarantined_at':str(now)}))
                            audit(c,UUID(intent['project_id']),'gc.quarantined','storage_operation',UUID(op))
                        report.append(dict(operation_id=op,decision='quarantined' if apply else 'eligible_for_quarantine'))
        return dict(mode='apply' if apply else 'dry_run',operations=report,journals_pruned=0,unknown_paths_policy='preserve')

    def check(self,*,mark_missing=False):
        result=[]
        with session_lock(self.engine,MAINTENANCE_KEY,shared=True),self.storage.lock('gate'):
            with unit_of_work(self.engine) as c:rows=c.execute(select(table('stored_files'))).mappings().all()
            for row in rows:
                state=row['state']
                try:self.checked(row['storage_key'],row);available=True
                except (OSError,StorageError,OpsError):available=False
                if not available and mark_missing and row['state']!='missing':
                    with unit_of_work(self.engine) as c:
                        c.exec_driver_sql('SELECT public.cc_lock_project(%s)',(row['project_id'],))
                        c.execute(update(table('stored_files')).where(table('stored_files').c.file_id==row['file_id']).values(state='missing',updated_at=func.clock_timestamp()))
                        audit(c,row['project_id'],'file.missing','stored_file',row['file_id'])
                    state='missing'
                result.append(dict(file_id=str(row['file_id']),state=state,bytes_verified=available,available=available and state=='stored'))
        return dict(files=result,all_available=all(item['available'] for item in result))

    def repair(self,file_id,raw):
        with unit_of_work(self.engine) as c:row=one(c,'stored_files','file_id',file_id)
        if len(raw)!=row['byte_size'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise OpsError('REPAIR_BYTES_MISMATCH')
        op=str(uuid4())
        with session_lock(self.engine,MAINTENANCE_KEY,shared=True),self.storage.lock('operation-'+op),self.storage.lock('gate'):
            try:self.checked(row['storage_key'],row);available=True
            except (OSError,StorageError,OpsError):available=False
            if not available:
                try:
                    with self.storage.read(row['storage_key']):pass
                except FileNotFoundError:pass
                else:move(self.storage,row['storage_key'],'quarantine/'+op+'/damaged-payload.bin')
                self.storage.write_new('staging/'+op+'/'+str(file_id),raw)
                self.storage.publish(op,dict(file_id=str(file_id),project_id=str(row['project_id']),storage_key=row['storage_key'],byte_size=str(row['byte_size']),sha256=row['sha256']))
            with unit_of_work(self.engine) as c:
                c.exec_driver_sql('SELECT public.cc_lock_project(%s)',(row['project_id'],))
                current=one(c,'stored_files','file_id',file_id,lock=True)
                if current['sha256']!=row['sha256'] or current['storage_key']!=row['storage_key']:raise OpsError('REPAIR_CONFLICT')
                c.execute(update(table('stored_files')).where(table('stored_files').c.file_id==file_id).values(state='stored',updated_at=func.clock_timestamp()))
                audit(c,row['project_id'],'file.repaired','stored_file',file_id)
        return dict(file_id=str(file_id),state='stored',expected_sha256=row['sha256'])
