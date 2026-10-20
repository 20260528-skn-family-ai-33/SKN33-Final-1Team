"""File publication precedes a short DB transaction; reconciliation never deletes."""
import hashlib
from uuid import UUID, uuid4
from sqlalchemy import select, insert
from ..storage.local import key, operation_key, StorageError
from ..storage.images import normalize, MAX_IMAGE
from ..persistence.transaction import unit_of_work
from ..persistence.database import PersistenceError
from ..security.errors import AuthError
from .business import table, audit


class Files:
    def __init__(self, engine, storage, business):
        self.engine, self.storage, self.business = engine, storage, business

    def preflight(self, subject, dev, pid):
        try:
            with unit_of_work(self.engine) as connection:
                self.business.project(connection, subject, pid, dev, 'write')
        except PersistenceError: raise AuthError('DATABASE_UNAVAILABLE', 503) from None

    def upload(self, subject, dev, pid, stream, request_id):
        op = str(uuid4())
        try:
            with self.storage.lock('operation-' + op):
                raw = stream.read(MAX_IMAGE + 1)
                display, measurements = normalize(raw)
                intent = dict(contract_version='cc-storage-journal-v1', operation='upload_image', operation_id=op,
                    project_id=str(pid), asset_id=str(uuid4()), image_id=str(uuid4()),
                    actor_id=str(subject.user_id), original_frame_id=str(uuid4()), display_frame_id=str(uuid4()),
                    measurements=measurements, phase='planned', db_outcome='not_started', files=[])
                for role, data in (('original_image', raw), ('display_image', display)):
                    fid = str(uuid4())
                    intent['files'].append(dict(file_id=fid, project_id=str(pid), file_role=role, storage_key=key(pid, fid),
                        byte_size=str(len(data)), sha256=hashlib.sha256(data).hexdigest()))
                self.storage.journal(intent)
                for item, data in zip(intent['files'], (raw, display)):
                    self.storage.write_new('staging/' + op + '/' + item['file_id'], data)
                intent['phase'] = 'staged'; self.storage.journal(intent)
                with self.storage.lock('gate', shared=True):
                    for item in intent['files']: self.storage.publish(op, item)
                    intent['phase'] = 'finalized'; intent['db_outcome'] = 'unconfirmed'; self.storage.journal(intent)
                    try:
                        self.persist(subject, dev, intent, request_id)
                    except AuthError:
                        intent['db_outcome'] = 'rolled_back'; self.storage.journal(intent)
                        raise
                    except PersistenceError:
                        # Fresh primary transaction + operation advisory try-lock resolves DB truth.
                        outcome = self.resolve(intent)
                        intent['db_outcome'] = outcome; self.storage.journal(intent)
                        if outcome != 'committed': raise AuthError('UPLOAD_OUTCOME_UNCONFIRMED', 503) from None
                    else:
                        intent['db_outcome'] = 'committed'; self.storage.journal(intent)
                return self.business.get_image(subject, dev, UUID(intent['image_id']))
        except (StorageError, OSError): raise AuthError('STORAGE_UNAVAILABLE', 503) from None

    def persist(self, subject, dev, intent, request_id):
        pid = UUID(intent['project_id']); original, display = intent['files']; measurements = intent['measurements']
        for attempt in range(3):
            try:
                with unit_of_work(self.engine) as connection:
                    connection.exec_driver_sql('SELECT pg_advisory_xact_lock(%s)', (operation_key(intent['operation_id']),))
                    if connection.exec_driver_sql('SELECT pg_is_in_recovery()').scalar_one(): raise AuthError('DATABASE_UNAVAILABLE', 503)
                    self.business.project(connection, subject, pid, dev, 'write')
                    for item in intent['files']:
                        mime = 'image/png' if item['file_role'] == 'display_image' else {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}[measurements['actual_format']]
                        connection.execute(insert(table('stored_files')).values(file_id=UUID(item['file_id']), project_id=pid,
                            parent_file_id=UUID(original['file_id']) if item['file_role'] == 'display_image' else None,
                            file_role=item['file_role'], storage_key=item['storage_key'], byte_size=int(item['byte_size']),
                            sha256=item['sha256'], detected_mime=mime, created_by=subject.user_id,
                            source_metadata={'operation_id': intent['operation_id']}))
                    connection.execute(insert(table('assets')).values(asset_id=UUID(intent['asset_id']), project_id=pid,
                        name='Image ' + intent['image_id'], created_by=subject.user_id))
                    connection.execute(insert(table('asset_versions')).values(asset_version_id=UUID(intent['image_id']), project_id=pid,
                        asset_id=UUID(intent['asset_id']), version_no=1, original_file_id=UUID(original['file_id']),
                        display_file_id=UUID(display['file_id']), original_sha256=original['sha256'],
                        **{name: measurements[name] for name in ('original_width', 'original_height', 'display_width', 'display_height')},
                        measurement_metadata=measurements, created_by=subject.user_id))
                    for role, item in (('original', original), ('display', display)):
                        connection.execute(insert(table('image_coordinate_frames')).values(frame_id=UUID(intent[role + '_frame_id']),
                            project_id=pid, asset_version_id=UUID(intent['image_id']), file_id=UUID(item['file_id']),
                            parent_frame_id=UUID(intent['original_frame_id']) if role == 'display' else None, frame_kind=role,
                            coordinate_space='normalized_original_pixels' if role == 'display' else 'original_pixels',
                            width=measurements[role + '_width'], height=measurements[role + '_height'],
                            transform_jsonb={'conversion_version': measurements['conversion_version'], 'exif_orientation': measurements['exif_orientation']}
                            if role == 'display' else None))
                    audit(connection, subject, pid, 'image.uploaded', 'asset_version', UUID(intent['image_id']), request_id, event_id=UUID(intent['operation_id']))
                return
            except PersistenceError as error:
                if error.retryable and attempt < 2: continue
                raise

    def resolve(self, intent):
        """No write retries or deletion on an unknown COMMIT. Fresh primary snapshot only."""
        try:
            with unit_of_work(self.engine) as connection:
                if connection.exec_driver_sql('SELECT pg_is_in_recovery()').scalar_one(): return 'unresolved'
                if not connection.exec_driver_sql('SELECT pg_try_advisory_xact_lock(%s)', (operation_key(intent['operation_id']),)).scalar_one(): return 'unresolved'
                expected = [('stored_files', 'file_id', item['file_id']) for item in intent['files']]
                expected += [('assets', 'asset_id', intent['asset_id']), ('asset_versions', 'asset_version_id', intent['image_id'])]
                expected += [('image_coordinate_frames', 'frame_id', intent[role + '_frame_id']) for role in ('original', 'display')]
                expected += [('audit_events', 'event_id', intent['operation_id'])]
                rows = [connection.execute(select(table(name)).where(table(name).c[pk] == UUID(identifier))).mappings().one_or_none()
                        for name, pk, identifier in expected]
                if all(row is None for row in rows): return 'not_committed'
                if any(row is None or str(row['project_id']) != intent['project_id'] for row in rows): return 'conflict'
                for item, row in zip(intent['files'], rows[:2]):
                    if any(str(row[field]) != item[field] for field in ('storage_key', 'file_role', 'byte_size', 'sha256')): return 'conflict'
                    if (row['state'] != 'stored' or row['storage_backend'] != 'local' or str(row['created_by']) != intent['actor_id']
                            or row['source_metadata'].get('operation_id') != intent['operation_id']): return 'conflict'
                    parent = intent['files'][0]['file_id'] if item['file_role'] == 'display_image' else None
                    if (str(row['parent_file_id']) if row['parent_file_id'] else None) != parent: return 'conflict'
                asset, image = rows[2:4]
                if (str(asset['created_by']) != intent['actor_id'] or str(image['asset_id']) != intent['asset_id']
                        or str(image['created_by']) != intent['actor_id'] or image['version_no'] != 1
                        or str(image['original_file_id']) != intent['files'][0]['file_id']
                        or str(image['display_file_id']) != intent['files'][1]['file_id']
                        or image['original_sha256'] != intent['files'][0]['sha256']
                        or image['measurement_metadata'] != intent['measurements']
                        or any(image[field] != intent['measurements'][field] for field in ('original_width','original_height','display_width','display_height'))): return 'conflict'
                for role, row, item in zip(('original', 'display'), rows[4:], intent['files']):
                    if (row['frame_kind'] != role or str(row['asset_version_id']) != intent['image_id']
                            or str(row['file_id']) != item['file_id'] or row['width'] != intent['measurements'][role + '_width']
                            or row['height'] != intent['measurements'][role + '_height']
                            or row['coordinate_space'] != ('original_pixels' if role == 'original' else 'normalized_original_pixels')
                            or (str(row['parent_frame_id']) if row['parent_frame_id'] else None) != (intent['original_frame_id'] if role == 'display' else None)):
                        return 'conflict'
                event = rows[-1]
                if (str(event['actor_user_id']) != intent['actor_id'] or event['action'] != 'image.uploaded'
                        or str(event['target_id']) != intent['image_id']): return 'conflict'
            return 'committed'
        except PersistenceError: return 'unresolved'

    def recover(self,*,concurrent=False):
        """Startup read/reconcile. Preserve unreferenced bytes until the C14 GC workflow."""
        outcomes = {}
        with self.storage.recovery_lock('gate',concurrent=concurrent) as locked:
            if not locked:return outcomes
            for intent in self.storage.journals():
                if intent['operation']!='upload_image': continue
                with self.storage.recovery_lock('operation-' + intent['operation_id'],concurrent=concurrent) as locked:
                    if not locked:continue
                    # Complete only this journal's transient publication link, never arbitrary hardlinks.
                    for item in intent['files']:
                        try:
                            with self.storage.read(item['storage_key'], links=2): pass
                        except FileNotFoundError: continue
                        except StorageError:
                            # Ordinary finalized payload has a single link and is verified below if referenced.
                            continue
                        else: self.storage.publish(intent['operation_id'], item)
                    outcome = self.resolve(intent)
                    if outcome == 'committed':
                        for item in intent['files']:
                            with self.storage.verified(item['storage_key'], int(item['byte_size']), item['sha256']): pass
                    if outcome in ('unresolved', 'conflict'): raise StorageError('STORAGE_RECOVERY_REQUIRED')
                    intent['db_outcome'] = outcome; self.storage.journal(intent)
                    outcomes[intent['operation_id']] = outcome
        return outcomes
