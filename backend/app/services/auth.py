"""Password verification is outside DB transactions; final auth decisions hold locks."""
from dataclasses import dataclass,field
import hashlib,hmac,logging,secrets
from sqlalchemy import func,select,update
from ..persistence.database import PersistenceError
from ..persistence.transaction import unit_of_work
from ..persistence.repositories import auth as repository
from ..persistence.schema.identity import users
from ..security.errors import AuthError
from ..security.csrf import session_token

logger=logging.getLogger('creative_checker.security')


def token_hash(raw):return hashlib.sha256(raw.encode('ascii')).hexdigest()


def public_user(row):
    return {'id':str(row['user_id']),'account_kind':row['account_kind'],'display_name':row['display_name'],'email':row['email']}


@dataclass(frozen=True,slots=True)
class Principal:
    user_id: object
    session_id: object
    account_kind: str
    display_name: str
    email: str | None
    token_hash: str=field(repr=False)

    def public(self):return public_user({'user_id':self.user_id,'account_kind':self.account_kind,'display_name':self.display_name,'email':self.email})


def principal(row):
    return Principal(row['user_id'],row['session_id'],row['account_kind'],row['display_name'],row['email'],row['token_hash'])


class AuthService:
    def __init__(self,engine,settings,passwords,limits):
        self.engine,self.settings,self.passwords,self.limits=engine,settings,passwords,limits

    def _development_setup(self,connection,row,allowed):
        if not allowed:return False
        if not row or not row['is_active'] or not repository.valid_development_setup(connection,row['user_id'],self.settings.developer_project):
            raise AuthError('AUTH_SETUP_REQUIRED',503)
        return True

    def current(self,raw,*,developer_allowed=False):
        try:
            with unit_of_work(self.engine) as connection:
                connection.exec_driver_sql('SELECT 1')
                if not session_token(raw):return None
                row=repository.session_state(connection,token_hash(raw))
                if not row:return None
                if row['account_kind']=='developer' and not self._development_setup(connection,row,developer_allowed):return None
                return principal(row)
        except PersistenceError:raise AuthError('AUTH_UNAVAILABLE',503) from None

    def login(self,credentials,old_raw,*,ip,developer_allowed,request_id):
        kind='email' if credentials.email is not None else 'developer'
        identifier=credentials.email if kind=='email' else credentials.username
        if kind=='developer' and not developer_allowed:raise AuthError('AUTH_ROUTE_NOT_FOUND',404)
        key=self.limits.before(ip,kind,identifier)
        success=False
        try:
            with unit_of_work(self.engine) as connection:
                row=(repository.user_by_email(connection,identifier) if kind=='email' else repository.developer(connection))
                snapshot=dict(row) if row else None
            if kind=='email':
                encoded=snapshot['password_hash'] if snapshot and snapshot['is_active'] else None
                verified,rehash=self.passwords.verify(credentials.password,encoded)
                verified=verified and snapshot is not None and snapshot['is_active']
            else:
                verified=(hmac.compare_digest(identifier.encode(),b'admin') and hmac.compare_digest(credentials.password.encode(),b'admin'))
                rehash=None
            if not verified:raise AuthError('INVALID_CREDENTIALS',401)
            if kind=='developer' and (not snapshot or not snapshot['is_active']):raise AuthError('AUTH_SETUP_REQUIRED',503)
            raw=secrets.token_urlsafe(32);old_hash=token_hash(old_raw) if session_token(old_raw) else None
            # A rehash updates users, whose M007 owner trigger reads every related project.
            # Lock those projects before users, and retry only a confirmed rolled-back topology change.
            for attempt in range(3):
                try:
                    with unit_of_work(self.engine) as connection:
                        old=repository.session(connection,old_hash) if old_hash else None
                        identities=sorted({snapshot['user_id']}|({old['user_id']} if old else set()))
                        related=repository.project_ids(connection,identities) if rehash else []
                        repository.lock_projects(connection,related)
                        locked={identity:repository.user(connection,identity,lock='update') for identity in identities}
                        if rehash and repository.project_ids(connection,identities)!=related:raise AuthError('AUTH_STATE_CHANGED',409)
                        current=locked[snapshot['user_id']]
                        if not current or not current['is_active'] or current['account_kind']!=kind or current['password_hash']!=snapshot['password_hash']:
                            raise AuthError('INVALID_CREDENTIALS',401)
                        if kind=='developer':self._development_setup(connection,current,developer_allowed)
                        if old_hash:
                            old=repository.session(connection,old_hash,lock=True)
                            if old:repository.revoke_session(connection,old['session_id'],connection.execute(select(func.clock_timestamp())).scalar_one())
                        if rehash:connection.execute(update(users).where(users.c.user_id==current['user_id']).values(password_hash=rehash,updated_at=func.clock_timestamp()))
                        now=connection.execute(select(func.clock_timestamp())).scalar_one()
                        repository.new_session(connection,current['user_id'],token_hash(raw),now)
                        result=public_user(current)
                    break
                except AuthError as error:
                    if error.code=='AUTH_STATE_CHANGED' and attempt<2:continue
                    raise
                except PersistenceError as error:
                    if error.retryable and attempt<2:continue
                    raise
            success=True
            logger.info('auth_login_success',extra={'event':'auth_login_success','request_id':request_id,'user_id':result['id']})
            return result,raw
        except PersistenceError:raise AuthError('AUTH_UNAVAILABLE',503) from None
        except AuthError as error:
            logger.info('auth_login_rejected',extra={'event':'auth_login_rejected','request_id':request_id,'reason':error.code})
            raise
        finally:self.limits.finished(key,success)

    def logout(self,raw,*,request_id):
        try:
            # Even an absent/expired cookie checks DB availability; a DB failure is never a successful logout.
            with unit_of_work(self.engine) as connection:
                connection.exec_driver_sql('SELECT 1')
                old=repository.session(connection,token_hash(raw)) if session_token(raw) else None
                if old:
                    repository.user(connection,old['user_id'],lock='update')
                    old=repository.session(connection,old['token_hash'],lock=True)
                    repository.revoke_session(connection,old['session_id'],connection.execute(select(func.clock_timestamp())).scalar_one())
            logger.info('auth_logout_success',extra={'event':'auth_logout_success','request_id':request_id})
        except PersistenceError:raise AuthError('AUTH_UNAVAILABLE',503) from None
