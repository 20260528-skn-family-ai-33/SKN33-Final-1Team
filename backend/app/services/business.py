"""C09 transaction orchestration. Each method returns a detached allowlist DTO."""
from datetime import datetime, timezone
from functools import wraps
import hashlib
from uuid import UUID, uuid4
from sqlalchemy import select, insert, update, func, and_, or_
from ..contracts.business import draft_payload, selected_items
from ..hashing.canonical import digest
from ..persistence.schema import metadata
from ..persistence.transaction import unit_of_work
from ..persistence.database import PersistenceError
from ..security.errors import AuthError
from .cursors import Cursors


def table(name): return metadata.tables['public.' + name]


def timestamp(value):
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ') if value else None


def safe_transaction(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        for attempt in range(3):
            try: return method(*args, **kwargs)
            except PersistenceError as error:
                if error.retryable and attempt < 2: continue
                raise AuthError('DATABASE_UNAVAILABLE', 503) from None
    return wrapped


def one(connection, name, identifier, value, *, lock=False):
    target = table(name)
    query = select(target).where(target.c[identifier] == value)
    if lock: query = query.with_for_update()
    row = connection.execute(query).mappings().one_or_none()
    if row is None: raise AuthError('RESOURCE_NOT_FOUND', 404)
    return dict(row)


def audit(connection, subject, project, action, kind, identifier, request_id, *, event_id=None):
    connection.execute(insert(table('audit_events')).values(event_id=event_id or uuid4(), project_id=project,
        actor_kind='developer' if subject.account_kind == 'developer' else 'user', actor_user_id=subject.user_id,
        action=action, target_type=kind, target_id=identifier, request_id=request_id, event_metadata={}))


class Business:
    def __init__(self, engine, authorization, settings):
        self.engine, self.authorization = engine, authorization
        self.cursors = Cursors(settings.cursor_secret)

    def project(self, connection, subject, pid, dev, action='read', **kwargs):
        return self.authorization.project(connection, subject, pid, action=action, developer_allowed=dev, **kwargs)

    def member(self, connection, subject, pid):
        target = table('project_members')
        return connection.execute(select(target.c.role).where(target.c.project_id == pid, target.c.user_id == subject.user_id,
                                                             target.c.is_active)).scalar_one()

    def project_dto(self, connection, subject, row):
        return dict(id=str(row['project_id']), name=row['name'], business_state=row['business_state'],
                    archived_at=timestamp(row['archived_at']), my_role=self.member(connection, subject, row['project_id']),
                    created_at=timestamp(row['created_at']), updated_at=timestamp(row['updated_at']))

    @safe_transaction
    def create_project(self, subject, dev, body, request_id):
        pid = uuid4()
        with unit_of_work(self.engine) as connection:
            self.authorization.identity(connection, subject, developer_allowed=dev, lock=True)
            connection.execute(insert(table('projects')).values(project_id=pid, created_by=subject.user_id, name=body.name))
            connection.execute(insert(table('project_members')).values(project_id=pid, user_id=subject.user_id, role='owner'))
            audit(connection, subject, pid, 'project.created', 'project', pid, request_id)
            result = self.project_dto(connection, subject, one(connection, 'projects', 'project_id', pid))
        return result

    @safe_transaction
    def get_project(self, subject, dev, pid):
        with unit_of_work(self.engine) as connection:
            return self.project_dto(connection, subject, self.project(connection, subject, pid, dev))

    @safe_transaction
    def patch_project(self, subject, dev, pid, body, request_id):
        with unit_of_work(self.engine) as connection:
            old = self.project(connection, subject, pid, dev, 'manage')
            values = {}
            if body.name is not None and body.name != old['name']: values['name'] = body.name
            if body.archived is not None and body.archived != (old['business_state'] == 'archived'):
                values.update(business_state='archived' if body.archived else 'active',
                              archived_at=func.clock_timestamp() if body.archived else None)
            if values:
                values['updated_at'] = func.clock_timestamp()
                connection.execute(update(table('projects')).where(table('projects').c.project_id == pid).values(**values))
                audit(connection, subject, pid, 'project.updated', 'project', pid, request_id)
            result = self.project_dto(connection, subject, one(connection, 'projects', 'project_id', pid))
        return result

    def same_project(self, connection, name, pk, identifier, pid):
        row = one(connection, name, pk, identifier)
        if row['project_id'] != pid: raise AuthError('RESOURCE_NOT_FOUND', 404)
        return row

    def sources(self, connection, source_documents, pid):
        for source in source_documents or []:
            self.same_project(connection, 'asset_versions', 'asset_version_id', UUID(source['asset_version_id']), pid)

    @safe_transaction
    def create_set(self, subject, dev, pid, body, request_id):
        identifier = uuid4()
        with unit_of_work(self.engine) as connection:
            self.project(connection, subject, pid, dev, 'write')
            connection.execute(insert(table('criteria_sets')).values(criteria_set_id=identifier, project_id=pid,
                name=body.name, created_by=subject.user_id))
            audit(connection, subject, pid, 'criteria_set.created', 'criteria_set', identifier, request_id)
            result = self.set_dto(one(connection, 'criteria_sets', 'criteria_set_id', identifier))
        return result

    @staticmethod
    def set_dto(row):
        return dict(id=str(row['criteria_set_id']), project_id=str(row['project_id']), name=row['name'],
                    created_at=timestamp(row['created_at']), updated_at=timestamp(row['updated_at']))

    @staticmethod
    def draft_dto(row):
        payload = draft_payload(row)
        return dict(id=str(row['draft_id']), **payload, revision=str(row['revision']), state=row['state'],
                    updated_by=str(row['updated_by']), created_at=timestamp(row['created_at']), updated_at=timestamp(row['updated_at']))

    @safe_transaction
    def create_draft(self, subject, dev, pid, body, request_id):
        identifier = uuid4(); content = body.model_dump()
        with unit_of_work(self.engine) as connection:
            self.project(connection, subject, pid, dev, 'write')
            self.same_project(connection, 'criteria_sets', 'criteria_set_id', UUID(body.criteria_set_id), pid)
            self.same_project(connection, 'asset_versions', 'asset_version_id', UUID(body.image_id), pid)
            self.sources(connection, content['source_documents'], pid)
            connection.execute(insert(table('criteria_drafts')).values(draft_id=identifier, project_id=pid,
                criteria_set_id=UUID(body.criteria_set_id), target_asset_version_id=UUID(body.image_id),
                raw_requirements=body.raw_requirements, items_jsonb=content['items'],
                source_documents_jsonb=content['source_documents'], updated_by=subject.user_id))
            audit(connection, subject, pid, 'criteria_draft.created', 'criteria_draft', identifier, request_id)
            result = self.draft_dto(one(connection, 'criteria_drafts', 'draft_id', identifier))
        return result

    @safe_transaction
    def get_draft(self, subject, dev, pid, identifier):
        with unit_of_work(self.engine) as connection:
            self.project(connection, subject, pid, dev)
            return self.draft_dto(self.same_project(connection, 'criteria_drafts', 'draft_id', identifier, pid))

    @safe_transaction
    def put_draft(self, subject, dev, pid, identifier, body, request_id):
        values = body.model_dump()
        with unit_of_work(self.engine) as connection:
            self.project(connection, subject, pid, dev, 'write')
            row = one(connection, 'criteria_drafts', 'draft_id', identifier, lock=True)
            if row['project_id'] != pid: raise AuthError('RESOURCE_NOT_FOUND', 404)
            if row['revision'] != int(body.expected_revision): raise AuthError('DRAFT_REVISION_CONFLICT', 409)
            if row['state'] != 'editing': raise AuthError('DRAFT_ARCHIVED', 409)
            self.sources(connection, values['source_documents'], pid)
            changed = (row['raw_requirements'] != body.raw_requirements or row['items_jsonb'] != values['items']
                       or row['source_documents_jsonb'] != values['source_documents'])
            if changed:
                if row['revision'] == 9223372036854775807: raise AuthError('REVISION_LIMIT_REACHED', 409)
                connection.execute(update(table('criteria_drafts')).where(table('criteria_drafts').c.draft_id == identifier).values(
                    raw_requirements=body.raw_requirements, items_jsonb=values['items'], source_documents_jsonb=values['source_documents'],
                    revision=row['revision'] + 1, updated_by=subject.user_id, updated_at=func.clock_timestamp()))
                audit(connection, subject, pid, 'criteria_draft.updated', 'criteria_draft', identifier, request_id)
            result = self.draft_dto(one(connection, 'criteria_drafts', 'draft_id', identifier))
        return result

    def version_dto(self, connection, row):
        items = table('criteria_items')
        criteria = [dict(criteria_item_id=str(item['criteria_item_id']), id=item['external_item_id'], position=item['position'],
                         text=item['text'], type=item['category'], source=item['source_text'], is_required=item['is_required'])
                    for item in connection.execute(select(items).where(items.c.criteria_version_id == row['criteria_version_id']).order_by(items.c.position)).mappings()]
        current = False
        if row['source_draft_id']:
            draft = one(connection, 'criteria_drafts', 'draft_id', row['source_draft_id'])
            current = draft['revision'] == row['source_draft_revision'] and draft['state'] == 'editing'
        return dict(id=str(row['criteria_version_id']), project_id=str(row['project_id']), criteria_set_id=str(row['criteria_set_id']),
                    image_id=str(row['target_asset_version_id']), version_no=row['version_no'], origin_kind=row['origin_kind'],
                    confirmation_state=row['confirmation_state'], source_draft_id=str(row['source_draft_id']) if row['source_draft_id'] else None,
                    source_draft_revision=str(row['source_draft_revision']) if row['source_draft_revision'] else None,
                    criteria_hash=row['criteria_hash'], hash_version=row['hash_version'],
                    confirmed_by=str(row['confirmed_by']) if row['confirmed_by'] else None,
                    confirmed_at=timestamp(row['confirmed_at']), criteria=criteria, draft_is_current=current)

    @safe_transaction
    def get_version(self, subject, dev, pid, identifier):
        with unit_of_work(self.engine) as connection:
            self.project(connection, subject, pid, dev)
            return self.version_dto(connection, self.same_project(connection, 'criteria_versions', 'criteria_version_id', identifier, pid))

    def replay(self, connection, subject, dev, pid, key, request_hash):
        target = table('criteria_versions')
        row = connection.execute(select(target).where(target.c.confirmed_by == subject.user_id, target.c.confirmation_key == key)).mappings().one_or_none()
        if row is None: return None
        if row['confirmation_request_hash'] != request_hash or row['project_id'] != pid:
            raise AuthError('IDEMPOTENCY_KEY_REUSED', 409)
        self.project(connection, subject, pid, dev, 'write', allow_archived=True)
        return self.version_dto(connection, row)

    @safe_transaction
    def confirm(self, subject, dev, pid, body, key, request_id):
        if key.version != 4: raise AuthError('INVALID_IDEMPOTENCY_KEY', 422)
        request_hash = digest('confirm_criteria', dict(actor_user_id=str(subject.user_id), operation='confirm_criteria',
            project_id=str(pid), body=body.model_dump()), 'cc-request-jcs-v1')
        with unit_of_work(self.engine) as connection:
            self.project(connection, subject, pid, dev, 'write', allow_archived=True)
            replay = self.replay(connection, subject, dev, pid, key, request_hash)
            if replay: return replay, True
            row = self.same_project(connection, 'criteria_drafts', 'draft_id', UUID(body.draft_id), pid)
            if row['revision'] != int(body.expected_revision): raise AuthError('DRAFT_REVISION_CONFLICT', 409)
            if row['state'] != 'editing': raise AuthError('DRAFT_ARCHIVED', 409)
            payload = draft_payload(row)
        # Hashing and snapshot construction do not hold any DB locks.
        normalized = selected_items(payload)
        draft_hash = digest('criteria_draft', payload)
        criteria_hash = digest('criteria_snapshot', dict(payload, items=normalized))
        identifier = uuid4()
        with unit_of_work(self.engine) as connection:
            # Global actor/key lock serializes replay even across different projects.
            advisory = int.from_bytes(hashlib.sha256((str(subject.user_id) + ':' + str(key)).encode()).digest()[:8], 'big', signed=True)
            connection.exec_driver_sql('SELECT pg_advisory_xact_lock(%s)', (advisory,))
            replay = self.replay(connection, subject, dev, pid, key, request_hash)
            if replay: return replay, True
            self.project(connection, subject, pid, dev, 'write')
            current = one(connection, 'criteria_drafts', 'draft_id', UUID(body.draft_id), lock=True)
            if current['project_id'] != pid: raise AuthError('RESOURCE_NOT_FOUND', 404)
            if current['revision'] != int(body.expected_revision) or draft_payload(current) != payload:
                raise AuthError('DRAFT_REVISION_CONFLICT', 409)
            if current['state'] != 'editing': raise AuthError('DRAFT_ARCHIVED', 409)
            self.sources(connection, payload['source_documents'], pid)
            versions = table('criteria_versions')
            number = connection.execute(select(func.coalesce(func.max(versions.c.version_no), 0)).where(versions.c.project_id == pid)).scalar_one() + 1
            if number > 2147483647: raise AuthError('VERSION_LIMIT_REACHED', 409)
            connection.execute(insert(versions).values(criteria_version_id=identifier, project_id=pid,
                criteria_set_id=current['criteria_set_id'], target_asset_version_id=current['target_asset_version_id'],
                source_draft_id=current['draft_id'], version_no=number, source_draft_revision=current['revision'],
                source_draft_hash=draft_hash, source_requirements_text=payload['raw_requirements'], source_documents_jsonb=payload['source_documents'],
                criteria_hash=criteria_hash, hash_version='cc-content-jcs-v1', origin_kind='service_confirmation',
                confirmation_state='confirmed', confirmed_by=subject.user_id, confirmed_at=func.clock_timestamp(),
                confirmation_key=key, confirmation_request_hash=request_hash, request_hash_version='cc-request-jcs-v1'))
            for item in normalized:
                connection.execute(insert(table('criteria_items')).values(criteria_item_id=uuid4(), project_id=pid,
                    target_asset_version_id=current['target_asset_version_id'], criteria_version_id=identifier,
                    external_item_id=item['external_item_id'], position=item['position'], category=item['category'], text=item['text'],
                    source_text=item['source_text'], source_item_jsonb=item['source_item']))
            connection.execute(update(versions).where(versions.c.criteria_version_id == identifier).values(sealed_at=func.clock_timestamp()))
            audit(connection, subject, pid, 'criteria_version.confirmed', 'criteria_version', identifier, request_id)
            result = self.version_dto(connection, one(connection, 'criteria_versions', 'criteria_version_id', identifier))
        return result, False

    def image_dto(self, connection, row):
        display = one(connection, 'stored_files', 'file_id', row['display_file_id'])
        frames = table('image_coordinate_frames')
        frame = connection.execute(select(frames.c.frame_id).where(frames.c.asset_version_id == row['asset_version_id'], frames.c.frame_kind == 'display')).scalar_one()
        return dict(id=str(row['asset_version_id']), asset_id=str(row['asset_id']), project_id=str(row['project_id']),
                    version_no=row['version_no'], width=row['display_width'], height=row['display_height'],
                    original_sha256=row['original_sha256'], display_sha256=display['sha256'],
                    actual_format=row['measurement_metadata']['actual_format'], content_type='image/png',
                    coordinate_space='normalized_original_pixels', frame_id=str(frame),
                    url='/api/v1/images/' + str(row['asset_version_id']) + '/content', created_at=timestamp(row['created_at']))

    @safe_transaction
    def get_image(self, subject, dev, identifier, *, content=False):
        with unit_of_work(self.engine) as connection:
            row = self.authorization.resource(connection, subject, 'asset_versions', identifier, developer_allowed=dev)
            if content:
                file = one(connection, 'stored_files', 'file_id', row['display_file_id'])
                if file['state'] != 'stored': raise AuthError('FILE_UNAVAILABLE', 503)
                return file
            return self.image_dto(connection, row)

    @safe_transaction
    def listing(self, subject, dev, kind, pid, filters, limit, cursor):
        names = {'projects': ('projects', 'project_id'), 'images': ('asset_versions', 'asset_version_id'),
                 'criteria-sets': ('criteria_sets', 'criteria_set_id'), 'criteria-drafts': ('criteria_drafts', 'draft_id'),
                 'criteria-versions': ('criteria_versions', 'criteria_version_id')}
        name, pk = names[kind]; target = table(name)
        context = dict(kind=kind, actor=str(subject.user_id), project=str(pid) if pid else None, filters=filters, limit=limit)
        last = self.cursors.read(cursor, context) if cursor is not None else None
        with unit_of_work(self.engine) as connection:
            self.authorization.identity(connection, subject, developer_allowed=dev)
            query = select(target)
            if pid:
                self.project(connection, subject, pid, dev)
                query = query.where(target.c.project_id == pid)
                for field, relation, key_name, column in (
                    ('image_id', 'asset_versions', 'asset_version_id', 'target_asset_version_id'),
                    ('criteria_set_id', 'criteria_sets', 'criteria_set_id', 'criteria_set_id')):
                    if filters.get(field):
                        identifier = UUID(filters[field])
                        self.same_project(connection, relation, key_name, identifier, pid)
                        query = query.where(target.c[column] == identifier)
            else:
                members = table('project_members')
                query = query.join(members, members.c.project_id == target.c.project_id).where(members.c.user_id == subject.user_id, members.c.is_active)
                archived = filters['archived']
                if archived != 'include': query = query.where(target.c.business_state == ('archived' if archived == 'only' else 'active'))
            order = target.c.version_no if kind == 'criteria-versions' else target.c.created_at
            if last:
                try:
                    position = int(last[0]) if kind == 'criteria-versions' else datetime.strptime(last[0], '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
                    identifier = UUID(last[1])
                except (ValueError, TypeError): raise AuthError('CURSOR_INVALID', 400) from None
                query = query.where(or_(order < position, and_(order == position, target.c[pk] < identifier)))
            rows = connection.execute(query.order_by(order.desc(), target.c[pk].desc()).limit(limit + 1)).mappings().all()
            has_more = len(rows) > limit; rows = rows[:limit]
            def dto(row):
                if kind == 'projects': return self.project_dto(connection, subject, row)
                if kind == 'images': return self.image_dto(connection, row)
                if kind == 'criteria-sets': return self.set_dto(row)
                if kind == 'criteria-drafts': return self.draft_dto(row)
                return self.version_dto(connection, row)
            items = [dto(row) for row in rows]
            next_cursor = None
            if has_more:
                row = rows[-1]
                position = row['version_no'] if kind == 'criteria-versions' else timestamp(row['created_at'])
                next_cursor = self.cursors.issue(context, [position, str(row[pk])])
        return dict(items=items, has_more=has_more, next_cursor=next_cursor)
