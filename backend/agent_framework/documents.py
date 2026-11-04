"""Authenticated M008 project PDFs: immutable bytes + AF002 version records.

Storage I/O precedes short transactions. Replays use the same operation journal;
an unconfirmed commit is reconciled read-only and never automatically reissued.
No parser output, criteria, approval or model execution is created here.
"""
from datetime import datetime
from hashlib import sha256
import json
import re
import unicodedata
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from pydantic import Field
from sqlalchemy import BigInteger, Column, Integer, MetaData, String, Table, insert, select
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID as PGUUID

from backend.app.hashing.canonical import loads
from backend.app.persistence.database import PersistenceError
from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from backend.app.storage.local import LocalStorage, StorageError, key, operation_key
from .contracts import Contract, DocumentRef
from .files import FileLimits, StoredFileMetadata, inspect_document
from .ports import AdapterFailure
from .requirement_text import inspect_requirement

PDF_MARKER = b'creative-checker-framework-pdf-v1\n'
metadata = MetaData(schema='agent_framework')
versions = Table('document_versions', metadata, *(
    Column(name, kind) for name, kind in (
        ('document_version_id',PGUUID(as_uuid=True)),('document_id',PGUUID(as_uuid=True)),
        ('project_id',PGUUID(as_uuid=True)),('version_no',BigInteger),('previous_version_id',PGUUID(as_uuid=True)),
        ('file_id',PGUUID(as_uuid=True)),('storage_key',String),('original_name',String),('media_type',String),
        ('byte_size',BigInteger),('sha256',String),('page_count',Integer),('created_by',PGUUID(as_uuid=True)),
        ('request_key',PGUUID(as_uuid=True)),('input_sha256',String),('created_at',TIMESTAMP(timezone=True)),
    )))


class RegisteredDocument(Contract):
    document_id: UUID
    project_id: UUID
    version_no: int = Field(ge=1)
    previous_version_id: UUID | None
    original_name: str
    byte_size: int = Field(gt=0)
    created_by: UUID
    created_at: datetime
    document: DocumentRef


class TextDocumentInput(Contract):
    text: str = Field(strict=True,min_length=1,max_length=60000)


def initialize_document_storage(root):
    """Explicit command only; LocalStorage.initialize refuses existing roots."""
    LocalStorage.initialize(root)
    with_storage = LocalStorage(root)
    try: with_storage.write_new('framework-pdf-marker', PDF_MARKER)
    finally: with_storage.close()


def open_document_storage(root):
    storage = LocalStorage(root)
    try:
        with storage.read('framework-pdf-marker') as stream:
            if stream.read(128) != PDF_MARKER: raise StorageError('FRAMEWORK_PDF_ROOT_INVALID')
        storage.start(shared=True)
        return storage
    except BaseException:
        storage.close()
        raise


def _name(value,media_type='application/pdf'):
    if not isinstance(value,str): raise AuthError('PDF_FILENAME_INVALID',422)
    value=unicodedata.normalize('NFC',value)
    suffix='.txt' if media_type=='text/plain' else '.pdf'
    if (not 1<=len(value)<=255 or value!=value.strip() or not value.lower().endswith(suffix)
            or re.search(r'[/\\\x00-\x1f\x7f\ud800-\udfff]',value)):
        raise AuthError('PDF_FILENAME_INVALID',422)
    return value


def _dto(row):
    return RegisteredDocument(document_id=row['document_id'],project_id=row['project_id'],
        version_no=row['version_no'],previous_version_id=row['previous_version_id'],
        original_name=row['original_name'],byte_size=row['byte_size'],created_by=row['created_by'],
        created_at=row['created_at'],document=DocumentRef(file_id=str(row['file_id']),
            document_version_id=str(row['document_version_id']),sha256=row['sha256'],page_count=row['page_count'],media_type=row['media_type']))


class PostgresDocuments:
    def __init__(self,engine,authorization,storage):
        self.engine,self.authorization,self.storage=engine,authorization,storage
        self.limits=FileLimits()

    def preflight(self,subject,dev,pid,document_id=None,previous_version_id=None):
        if (document_id is None)!=(previous_version_id is None):
            raise AuthError('DOCUMENT_PREVIOUS_VERSION_REQUIRED',422)
        try:
            with unit_of_work(self.engine) as c:
                self.authorization.project(c,subject,pid,action='write',developer_allowed=dev)
                if document_id is not None:
                    old=c.execute(select(versions).where(versions.c.project_id==pid,
                        versions.c.document_id==document_id,
                        versions.c.document_version_id==previous_version_id)).mappings().one_or_none()
                    if old is None:raise AuthError('RESOURCE_NOT_FOUND',404)
        except PersistenceError:raise AuthError('DATABASE_UNAVAILABLE',503) from None

    def _get(self,subject,dev,pid,version_id):
        with unit_of_work(self.engine) as c:
            self.authorization.project(c,subject,pid,developer_allowed=dev)
            row=c.execute(select(versions).where(versions.c.project_id==pid,
                versions.c.document_version_id==version_id)).mappings().one_or_none()
            if row is None:raise AuthError('RESOURCE_NOT_FOUND',404)
            return dict(row)

    def get(self,subject,dev,pid,version_id):
        try:return _dto(self._get(subject,dev,pid,version_id))
        except PersistenceError:raise AuthError('DATABASE_UNAVAILABLE',503) from None

    def history(self,subject,dev,pid,document_id):
        try:
            with unit_of_work(self.engine) as c:
                self.authorization.project(c,subject,pid,developer_allowed=dev)
                rows=c.execute(select(versions).where(versions.c.project_id==pid,
                    versions.c.document_id==document_id).order_by(versions.c.version_no)).mappings().all()
                if not rows:raise AuthError('RESOURCE_NOT_FOUND',404)
                return [_dto(r) for r in rows]
        except PersistenceError:raise AuthError('DATABASE_UNAVAILABLE',503) from None

    def content(self,subject,dev,pid,version_id):
        try:
            row=self._get(subject,dev,pid,version_id)
            with self.storage.verified(row['storage_key'],row['byte_size'],row['sha256']) as stream:
                raw=stream.read(self.limits.max_document_bytes+1)
            if (len(raw)!=row['byte_size'] or sha256(raw).hexdigest()!=row['sha256']
                    or inspect_requirement(raw,row['media_type'],self.limits)!=row['page_count']):
                raise AuthError('PDF_INTEGRITY_MISMATCH',503)
            return _dto(row),raw
        except PersistenceError:raise AuthError('DATABASE_UNAVAILABLE',503) from None
        except (StorageError,OSError,AdapterFailure):raise AuthError('PDF_INTEGRITY_MISMATCH',503) from None

    def catalog(self,subject,dev,pid,version_id):
        row=self._get(subject,dev,pid,version_id)
        return StoredFileMetadata(file_id=str(row['file_id']),project_id=str(pid),
            version_id=str(version_id),kind='text' if row['media_type']=='text/plain' else 'pdf',storage_key=row['storage_key'],
            media_type=row['media_type'],byte_size=row['byte_size'],sha256=row['sha256'])

    def _intent(self,op,pid,actor,request_key,input_hash,name,count,raw,document_id,previous,media_type='application/pdf'):
        journal_version='af-text-journal-v1' if media_type=='text/plain' else 'af-pdf-journal-v1'
        operation='register_text' if media_type=='text/plain' else 'register_pdf'
        try:
            with self.storage.read('operations/'+str(op)+'/journal.json') as stream:
                data=stream.read(65537)
            if len(data)>65536:raise StorageError('PDF_JOURNAL_INVALID')
            try:intent=loads(data)
            except AuthError:raise StorageError('PDF_JOURNAL_INVALID') from None
        except FileNotFoundError:
            file_id=uuid4()
            intent={'contract_version':journal_version,'operation_id':str(op),
                'operation':operation,'project_id':str(pid),'actor_id':str(actor),
                'request_key':str(request_key),'input_sha256':input_hash,
                'document_id':str(document_id or uuid4()),'previous_version_id':str(previous) if previous else None,
                'document_version_id':str(uuid4()),'original_name':name,'page_count':count,
                'phase':'planned','db_outcome':'not_started','files':[{
                    'file_id':str(file_id),'project_id':str(pid),'storage_key':key(pid,file_id),
                    'byte_size':str(len(raw)),'sha256':sha256(raw).hexdigest()}]}
            if media_type!='application/pdf':intent['media_type']=media_type
            self.storage.journal(intent)
        try:
            if (intent['contract_version']!=journal_version or intent['operation_id']!=str(op)
                    or intent['operation']!=operation or intent['project_id']!=str(pid)
                    or intent.get('media_type','application/pdf')!=media_type):
                raise StorageError('PDF_JOURNAL_INVALID')
            if intent['actor_id']!=str(actor) or intent['input_sha256']!=input_hash:
                raise AuthError('DOCUMENT_IDEMPOTENCY_CONFLICT',409)
            item=intent['files'][0]
            if (len(intent['files'])!=1 or intent['request_key']!=str(request_key)
                    or intent['original_name']!=name or intent['page_count']!=count
                    or intent['previous_version_id']!=(str(previous) if previous else None)
                    or document_id is not None and intent['document_id']!=str(document_id)
                    or item['project_id']!=str(pid) or item['storage_key']!=key(pid,item['file_id'])
                    or item['byte_size']!=str(len(raw)) or item['sha256']!=sha256(raw).hexdigest()):
                raise StorageError('PDF_JOURNAL_INVALID')
            UUID(intent['document_id']);UUID(intent['document_version_id'])
        except (KeyError,TypeError,ValueError,IndexError):raise StorageError('PDF_JOURNAL_INVALID') from None
        return intent

    def _publish(self,intent,raw):
        item=intent['files'][0];target=item['storage_key']
        try:
            with self.storage.verified(target,len(raw),item['sha256']):pass
            return
        except FileNotFoundError:pass
        except StorageError:
            # A crash may leave this operation's staging/publication hardlink.
            # publish verifies that both names address the same inode and bytes.
            with self.storage.read(target,links=2):pass
            self.storage.publish(intent['operation_id'],item)
            return
        stage='staging/'+intent['operation_id']+'/'+item['file_id']
        try:
            with self.storage.read(stage) as stream:
                if stream.read(len(raw)+1)!=raw:raise StorageError('PDF_STAGING_CONFLICT')
        except FileNotFoundError:self.storage.write_new(stage,raw)
        self.storage.publish(intent['operation_id'],item)

    @staticmethod
    def _matches(row,intent):
        item=intent['files'][0]
        expected={
            'document_version_id':intent['document_version_id'],'document_id':intent['document_id'],
            'project_id':intent['project_id'],'previous_version_id':intent['previous_version_id'],
            'created_by':intent['actor_id'],'request_key':intent['request_key'],
            'input_sha256':intent['input_sha256'],'original_name':intent['original_name'],
            'page_count':str(intent['page_count']),'file_id':item['file_id'],
            'byte_size':item['byte_size'],'sha256':item['sha256'],'storage_key':item['storage_key'],
            'media_type':intent.get('media_type','application/pdf'),
        }
        return all((str(row[k]) if row[k] is not None else None)==v for k,v in expected.items())

    def _persist(self,subject,dev,intent):
        pid=UUID(intent['project_id']);op=UUID(intent['operation_id'])
        with unit_of_work(self.engine) as c:
            self.authorization.project(c,subject,pid,action='write',developer_allowed=dev)
            c.exec_driver_sql('SELECT pg_advisory_xact_lock(%s)',(operation_key(op),))
            if c.exec_driver_sql('SELECT pg_is_in_recovery()').scalar_one():raise AuthError('DATABASE_UNAVAILABLE',503)
            old=c.execute(select(versions).where(versions.c.project_id==pid,
                versions.c.request_key==UUID(intent['request_key']))).mappings().one_or_none()
            if old:
                if not self._matches(old,intent):raise AuthError('DOCUMENT_IDEMPOTENCY_CONFLICT',409)
                return dict(old)
            doc_id=UUID(intent['document_id']);previous=intent['previous_version_id']
            latest=c.execute(select(versions).where(versions.c.project_id==pid,
                versions.c.document_id==doc_id).order_by(versions.c.version_no.desc()).limit(1)).mappings().one_or_none()
            if previous:
                if latest is None:raise AuthError('RESOURCE_NOT_FOUND',404)
                if str(latest['document_version_id'])!=previous:raise AuthError('DOCUMENT_VERSION_CONFLICT',409)
            elif latest is not None:raise AuthError('DOCUMENT_VERSION_CONFLICT',409)
            number=latest['version_no']+1 if latest else 1
            if number>9223372036854775807:raise AuthError('DOCUMENT_VERSION_CONFLICT',409)
            item=intent['files'][0]
            row=c.execute(insert(versions).values(document_version_id=UUID(intent['document_version_id']),
                document_id=doc_id,project_id=pid,version_no=number,
                previous_version_id=UUID(previous) if previous else None,file_id=UUID(item['file_id']),
                storage_key=item['storage_key'],original_name=intent['original_name'],media_type=intent.get('media_type','application/pdf'),
                byte_size=int(item['byte_size']),sha256=item['sha256'],page_count=intent['page_count'],
                created_by=subject.user_id,request_key=UUID(intent['request_key']),input_sha256=intent['input_sha256']
            ).returning(versions)).mappings().one()
            return dict(row)

    def _reconcile(self,intent):
        # Read-only reconciliation on a new primary connection, never a write retry.
        with unit_of_work(self.engine) as c:
            if c.exec_driver_sql('SELECT pg_is_in_recovery()').scalar_one():return None
            if not c.exec_driver_sql('SELECT pg_try_advisory_xact_lock(%s)',
                    (operation_key(intent['operation_id']),)).scalar_one():return None
            row=c.execute(select(versions).where(versions.c.project_id==UUID(intent['project_id']),
                versions.c.request_key==UUID(intent['request_key']))).mappings().one_or_none()
            return dict(row) if row and self._matches(row,intent) else None

    def register(self,subject,dev,pid,request_key,raw,*,filename='document.pdf',document_id=None,previous_version_id=None,media_type='application/pdf'):
        self.preflight(subject,dev,pid,document_id,previous_version_id)
        if media_type not in ('application/pdf','text/plain'):raise AuthError('UNSUPPORTED_MEDIA_TYPE',415)
        if media_type=='text/plain':
            from .postgres_schema import check_extension
            with unit_of_work(self.engine) as c:
                if check_extension(c)['revision']!='AF004':raise AuthError('FRAMEWORK_TEXT_SCHEMA_NOT_READY',503)
        name=_name(filename,media_type)
        try:count=inspect_requirement(raw,media_type,self.limits)
        except AdapterFailure as e:raise AuthError(e.code,413 if e.code=='FILE_TOO_LARGE' else 422) from None
        fixed={'project_id':str(pid),'actor_id':str(subject.user_id),'sha256':sha256(raw).hexdigest(),
            'byte_size':len(raw),'page_count':count,'filename':name,'document_id':str(document_id) if document_id else None,
            'previous_version_id':str(previous_version_id) if previous_version_id else None}
        if media_type!='application/pdf':fixed['media_type']=media_type
        fingerprint=sha256(json.dumps(fixed,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        op=uuid5(NAMESPACE_URL,('creative-checker-text:' if media_type=='text/plain' else 'creative-checker-pdf:')+str(pid)+':'+str(request_key))
        try:
            with self.storage.lock('operation-'+str(op)):
                intent=self._intent(op,pid,subject.user_id,request_key,fingerprint,name,count,raw,document_id,previous_version_id,media_type)
                self._publish(intent,raw)
                intent.update(phase='finalized',db_outcome='unconfirmed');self.storage.journal(intent)
                try:row=self._persist(subject,dev,intent)
                except PersistenceError:
                    row=self._reconcile(intent)
                    if row is None:raise AuthError('DOCUMENT_UPLOAD_OUTCOME_UNCONFIRMED',503) from None
                intent['db_outcome']='committed';self.storage.journal(intent)
                # Recheck the session/project at the final response boundary.
                return self.get(subject,dev,pid,row['document_version_id'])
        except PersistenceError:raise AuthError('DOCUMENT_UPLOAD_OUTCOME_UNCONFIRMED',503) from None
        except (StorageError,OSError):raise AuthError('DOCUMENT_STORAGE_UNAVAILABLE',503) from None


class RegisteredProjectCatalog:
    """Worker-facing catalog; binds an authenticated identity, not request actor IDs."""
    def __init__(self,documents,subject,*,developer_allowed=False):
        self.documents,self.subject,self.dev=documents,subject,developer_allowed
        from .adapters.project_files import LegacyPosterCatalog
        self.posters=LegacyPosterCatalog(documents.engine,documents.authorization,subject,developer_allowed)

    def lookup_document(self,context,reference):
        from .adapters.project_files import _authorize_error
        if context.actor_id!=str(self.subject.user_id):raise AdapterFailure('FILE_ACCESS_DENIED','The bound identity cannot access this file.')
        if context.document_version_id!=reference.document_version_id:
            raise AdapterFailure('FILE_REFERENCE_MISMATCH','The document version does not match.')
        try:return self.documents.catalog(self.subject,self.dev,UUID(context.project_id),UUID(reference.document_version_id))
        except AuthError as e:_authorize_error(e)
        except (PersistenceError,ValueError):raise AdapterFailure('FILE_STORAGE_UNAVAILABLE','Document metadata could not be read.') from None

    def lookup_asset(self,context,reference):return self.posters.lookup_asset(context,reference)
