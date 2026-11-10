"""Owner-scoped queue controls and reversible, append-only history visibility.

AF001–AF005 are shared with older workers: cancellation uses the existing failed
terminal state plus USER_CANCELLED, exposed as cancelled by this view. No schema
change, deletion, retry, approval or model call is performed here.
"""
from pydantic import StrictBool
from sqlalchemy import select, exists, func, and_, or_

from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from backend.app.services.business import table
from .contracts import Contract, FrameworkError, RunSnapshot
from .postgres import runs, jobs, events

CANCELLED = 'USER_CANCELLED'
FINISHED = ('completed', 'failed')
VISIBILITY_EVENT = 'history.visibility_changed'


class HistoryVisibility(Contract):
    hidden: StrictBool


class MyTasks:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _hidden(actor_id):
        latest = (select(events.c.metadata_json['hidden'].as_boolean())
                  .where(events.c.run_id == runs.c.run_id,
                         events.c.kind == VISIBILITY_EVENT,
                         events.c.metadata_json['actor_id'].as_string() == str(actor_id))
                  .order_by(events.c.recorded_at.desc(), events.c.event_id.desc()).limit(1)
                  .correlate(runs).scalar_subquery())
        return func.coalesce(latest, False)

    def list(self, subject, *, visibility='visible', limit=30, before=None):
        members, projects = table('project_members'), table('projects')
        hidden = self._hidden(subject.user_id)
        pending = exists(select(jobs.c.job_id).where(jobs.c.run_id == runs.c.run_id,
                         jobs.c.state.in_(('queued', 'running')))).correlate(runs)
        queued = exists(select(jobs.c.job_id).where(jobs.c.run_id == runs.c.run_id,
                        jobs.c.state == 'queued')).correlate(runs)
        query = (select(runs.c.run_id, runs.c.project_id, runs.c.snapshot_json,
                        runs.c.created_at, projects.c.name.label('project_name'),
                        members.c.role, hidden.label('hidden'), pending.label('pending'),
                        queued.label('queued'))
                 .join(projects, projects.c.project_id == runs.c.project_id)
                 .join(members, and_(members.c.project_id == runs.c.project_id,
                                    members.c.user_id == subject.user_id))
                 .where(runs.c.actor_id == subject.user_id, members.c.is_active,
                        members.c.role.in_(('owner', 'editor', 'viewer'))))
        with unit_of_work(self.store.engine) as c:
            self.store.authorization.identity(c, subject, developer_allowed=False)
            if before:
                anchor = c.execute(query.where(runs.c.run_id == before)).mappings().one_or_none()
                if anchor is None:
                    raise AuthError('FRAMEWORK_TASK_CURSOR_INVALID', 422)
                query = query.where(or_(runs.c.created_at < anchor['created_at'],
                    and_(runs.c.created_at == anchor['created_at'], runs.c.run_id < before)))
            if visibility != 'all':
                query = query.where(hidden == (visibility == 'hidden'))
            rows = c.execute(query.order_by(runs.c.created_at.desc(), runs.c.run_id.desc())
                             .limit(limit + 1)).mappings().all()
            items = []
            for row in rows[:limit]:
                snapshot = row['snapshot_json']
                code = (snapshot.get('error') or {}).get('code')
                state = 'cancelled' if code == CANCELLED else snapshot['execution_status']
                items.append(dict(run_id=row['run_id'], project_id=str(row['project_id']),
                    project_name=row['project_name'], created_at=row['created_at'].isoformat(),
                    state=state, kind='poster' if snapshot.get('baseline') else 'document',
                    error_code=code, hidden=row['hidden'],
                    can_cancel=row['queued'] and snapshot['execution_status'] == 'queued'
                               and row['role'] in ('owner', 'editor'),
                    can_hide=not row['pending'] and snapshot['execution_status'] in FINISHED))
            return dict(items=items, has_more=len(rows) > limit,
                        next_cursor=items[-1]['run_id'] if len(rows) > limit else None)

    def _owned_locked(self, c, subject, run_id, *, write):
        row = self.store._row(c, run_id)
        # Do not disclose someone else's run even to another project owner.
        if row['actor_id'] != subject.user_id:
            raise AuthError('FRAMEWORK_RUN_NOT_FOUND', 404)
        project = table('projects')
        # Same project -> run -> job lock order as claim_next and recovery.
        c.execute(select(project.c.project_id).where(project.c.project_id == row['project_id'])
                  .with_for_update(key_share=True))
        self.store.authorization.project(c, subject, row['project_id'],
                                         action='write' if write else 'read', allow_archived=True)
        return self.store._row(c, run_id, lock=True)

    def cancel(self, subject, run_id):
        from .durable_worker import PostgresQueue
        with unit_of_work(self.store.engine) as c:
            row = self._owned_locked(c, subject, run_id, write=True)
            snapshot = RunSnapshot.model_validate(row['snapshot_json'])
            if snapshot.error and snapshot.error.code == CANCELLED:
                return snapshot  # Idempotent retry after a lost HTTP response.
            active = c.execute(select(jobs).where(jobs.c.run_id == run_id,
                               jobs.c.state.in_(('queued', 'running'))).with_for_update()).mappings().all()
            if (snapshot.execution_status != 'queued' or len(active) != 1
                    or active[0]['state'] != 'queued'
                    or any(step.status == 'running' for step in snapshot.step_records)):
                raise AuthError('FRAMEWORK_TASK_NOT_QUEUED', 409)
            PostgresQueue(self.store)._close(c, row, dict(active[0]),
                FrameworkError(code=CANCELLED, message='요청자가 대기 작업을 취소했습니다.'))
            self.store.event(c, run_id, 'job.cancelled', actor_id=str(subject.user_id),
                             phase=active[0]['phase'])
            return RunSnapshot.model_validate(self.store._row(c, run_id)['snapshot_json'])

    def visibility(self, subject, run_id, hidden):
        with unit_of_work(self.store.engine) as c:
            row = self._owned_locked(c, subject, run_id, write=False)
            active = c.execute(select(jobs.c.job_id).where(jobs.c.run_id == run_id,
                               jobs.c.state.in_(('queued', 'running')))).first()
            if hidden and (active or row['snapshot_json']['execution_status'] not in FINISHED):
                raise AuthError('FRAMEWORK_TASK_NOT_FINISHED', 409)
            current = c.execute(select(self._hidden(subject.user_id)).select_from(runs)
                                .where(runs.c.run_id == run_id)).scalar_one()
            if current != hidden:
                self.store.event(c, run_id, VISIBILITY_EVENT, actor_id=str(subject.user_id), hidden=hidden)
            return dict(run_id=run_id, hidden=hidden)
