"""Reusable current-state authorization for future business repositories; no HTTP/commit."""
from sqlalchemy import select,func
from ..persistence.repositories import auth as repository
from ..persistence.schema.identity import projects,project_members
from ..persistence.schema import metadata
from ..security.errors import AuthError


class Authorization:
    def __init__(self,settings):self.settings=settings

    def identity(self,connection,subject,*,developer_allowed,lock=False):
        if subject is None:raise AuthError('AUTHENTICATION_REQUIRED',401)
        user=repository.user(connection,subject.user_id,lock='share' if lock else None)
        query=select(metadata.tables['public.user_sessions']).where(metadata.tables['public.user_sessions'].c.session_id==subject.session_id)
        if lock:query=query.with_for_update(read=True)
        session=connection.execute(query).mappings().one_or_none()
        now=connection.execute(select(func.clock_timestamp())).scalar_one()
        if (not user or not user['is_active'] or not session or session['user_id']!=subject.user_id
            or session['token_hash']!=subject.token_hash or session['revoked_at'] is not None or now>=session['expires_at']):
            raise AuthError('AUTHENTICATION_REQUIRED',401)
        if user['account_kind']=='developer':
            if not developer_allowed:raise AuthError('AUTHENTICATION_REQUIRED',401)
            if not repository.valid_development_setup(connection,user['user_id'],self.settings.developer_project):raise AuthError('AUTH_SETUP_REQUIRED',503)
        return user

    def project(self,connection,subject,project_id,*,action='read',developer_allowed=False,allow_archived=False):
        if action not in ('read','write','manage','restore'):raise ValueError('Unsupported authorization action')
        query=select(projects).where(projects.c.project_id==project_id)
        # Writes serialize with archival/membership changes. Reads use the current committed snapshot.
        if action!='read':query=query.with_for_update(key_share=True)
        project=connection.execute(query).mappings().one_or_none()
        self.identity(connection,subject,developer_allowed=developer_allowed,lock=action!='read')
        member_query=select(project_members).where(project_members.c.project_id==project_id,project_members.c.user_id==subject.user_id)
        if action!='read':member_query=member_query.with_for_update(read=True)
        member=connection.execute(member_query).mappings().one_or_none()
        if not project or not member or not member['is_active']:raise AuthError('RESOURCE_NOT_FOUND',404)
        allowed={'read':('viewer','editor','owner'),'write':('editor','owner'),'manage':('owner',),'restore':('owner',)}[action]
        if member['role'] not in allowed:raise AuthError('PROJECT_PERMISSION_DENIED',403)
        if action=='write' and not allow_archived and project['business_state']!='active':raise AuthError('PROJECT_ARCHIVED',409)
        return dict(project)

    def resource(self,connection,subject,table_name,resource_id,*,expected_project=None,action='read',developer_allowed=False):
        self.identity(connection,subject,developer_allowed=developer_allowed)
        keys={'stored_files':'file_id','asset_versions':'asset_version_id','criteria_drafts':'draft_id',
            'criteria_versions':'criteria_version_id','review_runs':'run_id','item_results':'result_id'}
        if table_name not in keys:raise ValueError('Unsupported protected resource')
        table=metadata.tables['public.'+table_name]
        row=connection.execute(select(table).where(table.c[keys[table_name]]==resource_id)).mappings().one_or_none()
        if not row or expected_project is not None and row['project_id']!=expected_project:raise AuthError('RESOURCE_NOT_FOUND',404)
        self.project(connection,subject,row['project_id'],action=action,developer_allowed=developer_allowed)
        return dict(row)

    def schema_for_validation(self,connection,subject,project_id,schema_id,*,developer_allowed=False):
        self.project(connection,subject,project_id,action='write',developer_allowed=developer_allowed)
        table=metadata.tables['public.schema_definitions']
        row=connection.execute(select(table).where(table.c.schema_id==schema_id)).mappings().one_or_none()
        if not row or row['availability_state']!='available' or row['access_scope']!='service_shared':raise AuthError('RESOURCE_NOT_FOUND',404)
        return dict(row)
