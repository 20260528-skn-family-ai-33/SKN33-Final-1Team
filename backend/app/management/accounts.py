"""Protected owner-only operations. Never imported by the web authentication service."""
import logging
from uuid import uuid4
from sqlalchemy import select,update,func,text
from ..persistence.database import PersistenceError
from ..persistence.transaction import unit_of_work
from ..persistence.repositories import auth as repository
from ..persistence.schema.identity import users,user_sessions,projects,project_members
from ..persistence.schema.ocr import audit_events
from ..security.passwords import normalize_email
from ..security.errors import AuthError
from ..services.auth import public_user

logger=logging.getLogger('creative_checker.security')


class AccountManager:
    def __init__(self,engine,passwords,*,operator):
        if not isinstance(operator,str) or not operator.strip() or len(operator)>100:raise AuthError('INVALID_AUTH_REQUEST',422)
        self.engine,self.passwords,self.operator=engine,passwords,operator

    def _run(self,operation,*,unique_retry=False):
        for attempt in range(3):
            try:
                with unit_of_work(self.engine) as connection:
                    valid=connection.exec_driver_sql("""SELECT current_user=d.datdba::regrole::text AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls)
                        FROM pg_database d JOIN pg_roles r ON r.rolname=current_user WHERE d.datname=current_database()""").scalar_one()
                    if not valid:raise AuthError('MANAGEMENT_FORBIDDEN',403)
                    result=operation(connection)
                return result
            except AuthError as error:
                if error.code=='AUTH_STATE_CHANGED' and attempt<2:continue
                raise
            except PersistenceError as error:
                if error.outcome=='rolled_back' and (error.retryable or unique_retry and error.sqlstate=='23505') and attempt<2:continue
                if error.sqlstate=='23505':raise AuthError('DEVELOPMENT_CONFLICT' if unique_retry else 'EMAIL_ALREADY_EXISTS',409) from None
                raise AuthError('AUTH_UNAVAILABLE',503) from None

    def _locks(self,connection,identities,extra_projects=()):
        before=repository.project_ids(connection,identities)
        related=sorted(set(before)|set(extra_projects))
        repository.lock_projects(connection,related)
        locked={uid:repository.user(connection,uid,lock='update') for uid in sorted(set(identities))}
        if repository.project_ids(connection,identities)!=before:raise AuthError('AUTH_STATE_CHANGED',409)
        connection.execute(select(user_sessions.c.session_id).where(user_sessions.c.user_id.in_(identities)).order_by(user_sessions.c.session_id).with_for_update()).all()
        connection.execute(select(project_members.c.project_id).where(project_members.c.user_id.in_(identities)).order_by(project_members.c.project_id,project_members.c.user_id).with_for_update()).all()
        return locked,related

    def _owners(self,connection,related):
        for project in related:
            if connection.execute(select(projects.c.project_id).where(projects.c.project_id==project)).first() and not connection.execute(
                select(project_members.c.user_id).join(users,users.c.user_id==project_members.c.user_id).where(
                    project_members.c.project_id==project,project_members.c.is_active,project_members.c.role=='owner',users.c.is_active)).first():
                raise AuthError('LAST_OWNER_REQUIRED',409)

    def _audit(self,connection,related,action,target_type,target_id,changes):
        for project in related:
            connection.execute(audit_events.insert().values(event_id=uuid4(),project_id=project,actor_kind='system',
                action=action,target_type=target_type,target_id=target_id,event_metadata={'operator':self.operator,**changes}))

    def create_email_user(self,email,display_name,password):
        email=normalize_email(email)
        if not isinstance(display_name,str) or not display_name.strip() or len(display_name)>100 or '\x00' in display_name or any(0xD800<=ord(c)<=0xDFFF for c in display_name):raise AuthError('INVALID_AUTH_REQUEST',422)
        encoded=self.passwords.hash(password)
        def operation(connection):
            row=connection.execute(users.insert().values(user_id=uuid4(),account_kind='email',email=email,password_hash=encoded,
                display_name=display_name).returning(users)).mappings().one()
            return public_user(row)
        result=self._run(operation)
        logger.info('account_created',extra={'event':'account_created','user_id':result['id'],'operator':self.operator})
        return result

    def _change_user(self,user_id,patch,*,action,email_only=False):
        def operation(connection):
            locked,related=self._locks(connection,[user_id]);row=locked[user_id]
            if not row:raise AuthError('RESOURCE_NOT_FOUND',404)
            if email_only and row['account_kind']!='email':raise AuthError('INVALID_AUTH_REQUEST',422)
            now=connection.execute(select(func.clock_timestamp())).scalar_one()
            connection.execute(update(users).where(users.c.user_id==user_id).values(**patch,updated_at=now))
            repository.revoke_all(connection,user_id,now)
            self._owners(connection,related)
            self._audit(connection,related,action,'user',user_id,{'changed_fields':sorted(patch),
                'before_active':row['is_active'],'after_active':patch.get('is_active',row['is_active'])})
            return {'id':str(user_id),'action':action}
        result=self._run(operation)
        logger.info('account_changed',extra={'event':action,'user_id':str(user_id),'operator':self.operator})
        return result

    def reset_password(self,user_id,password):
        encoded=self.passwords.hash(password)
        return self._change_user(user_id,{'password_hash':encoded},action='account.password_reset',email_only=True)

    def change_email(self,user_id,email):
        return self._change_user(user_id,{'email':normalize_email(email)},action='account.email_changed',email_only=True)

    def set_user_active(self,user_id,active):
        if type(active) is not bool:raise AuthError('INVALID_AUTH_REQUEST',422)
        # Activation also revokes any old unrevoked sessions; no session resurrection.
        return self._change_user(user_id,{'is_active':active},action='account.activation_changed')

    def set_project_member(self,project_id,user_id,role,active):
        if role not in ('owner','editor','viewer') or type(active) is not bool:raise AuthError('INVALID_AUTH_REQUEST',422)
        def operation(connection):
            locked,_=self._locks(connection,[user_id],[project_id])
            if not locked[user_id] or not connection.execute(select(projects.c.project_id).where(projects.c.project_id==project_id)).first():raise AuthError('RESOURCE_NOT_FOUND',404)
            if active and not locked[user_id]['is_active']:raise AuthError('AUTH_STATE_CHANGED',409)
            old=connection.execute(select(project_members).where(project_members.c.project_id==project_id,project_members.c.user_id==user_id)).mappings().one_or_none()
            patch={'role':role,'is_active':active}
            if old:
                connection.execute(update(project_members).where(project_members.c.project_id==project_id,project_members.c.user_id==user_id).values(**patch,updated_at=func.clock_timestamp()))
            else:connection.execute(project_members.insert().values(project_id=project_id,user_id=user_id,**patch))
            self._owners(connection,[project_id])
            self._audit(connection,[project_id],'membership.changed','user',user_id,{'before':{'role':old['role'],'is_active':old['is_active']} if old else None,'after':patch})
            return {'project_id':str(project_id),'user_id':str(user_id),**patch}
        return self._run(operation)

    def bootstrap_development(self,project_id,*,environment,enabled):
        if environment not in ('development','test') or not enabled:raise AuthError('MANAGEMENT_FORBIDDEN',403)
        def operation(connection):
            row=repository.developer(connection)
            if row:
                locked,related=self._locks(connection,[row['user_id']],[project_id]);row=locked[row['user_id']]
                if not row['is_active']:raise AuthError('AUTH_SETUP_REQUIRED',503)
            else:
                repository.lock_projects(connection,[project_id])
                row=connection.execute(users.insert().values(user_id=uuid4(),account_kind='developer',display_name='관리자 (개발용)').returning(users)).mappings().one()
            project=connection.execute(select(projects).where(projects.c.project_id==project_id)).mappings().one_or_none()
            member=connection.execute(select(project_members).where(project_members.c.project_id==project_id,project_members.c.user_id==row['user_id'])).mappings().one_or_none()
            if project:
                members=connection.execute(select(project_members).where(project_members.c.project_id==project_id)).mappings().all()
                if (project['created_by']!=row['user_id'] or project['business_state']!='active' or not member or not member['is_active']
                    or member['role']!='owner' or len(members)!=1):raise AuthError('DEVELOPMENT_CONFLICT',409)
            else:
                if repository.project_ids(connection,[row['user_id']]):raise AuthError('DEVELOPMENT_CONFLICT',409)
                connection.execute(projects.insert().values(project_id=project_id,created_by=row['user_id'],name='개발 프로젝트'))
                connection.execute(project_members.insert().values(project_id=project_id,user_id=row['user_id'],role='owner'))
                self._audit(connection,[project_id],'development.bootstrapped','project',project_id,{'created':True})
            return {'user':public_user(row),'project_id':str(project_id)}
        return self._run(operation,unique_retry=True)
