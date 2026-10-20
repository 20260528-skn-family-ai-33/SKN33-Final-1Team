"""M006 validates the whole FK graph without bypassing existing constraints."""
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_identity_migration_integration import owner_rollback
from backend.tests.relation_fixtures import create_graph, insert_row, row_where
from backend.tests.schema_helpers import foreign_key_contract

pytestmark = pytest.mark.db_integration
FK_CONTRACT = foreign_key_contract()


@pytest.fixture(scope='module')
def relations_database(database, tmp_path_factory):
    from alembic import command
    from alembic.config import Config
    from alembic.util import CommandError
    from sqlalchemy import delete
    from backend.app.persistence.schema import metadata
    from backend.migrations.configuration import create_migration_engine, load_migration_settings
    env = database.environment
    settings = load_migration_settings({'DB_HOST':env['TEST_DB_HOST'], 'DB_PORT':env['TEST_DB_PORT'], 'POSTGRES_DB':database.name,
        'DB_OWNER_USER':env['TEST_DB_OWNER_USER'], 'DB_OWNER_PASSWORD':env['TEST_DB_OWNER_PASSWORD'], 'DB_APP_USER':env['TEST_DB_APP_USER']})
    def configuration(directory=None):
        config = Config(str(Path('backend/migrations/alembic.ini').resolve()))
        config.attributes['migration_settings'] = settings
        if directory:
            config.set_main_option('script_location', str(directory))
        return config
    owner = create_migration_engine(settings)
    def snapshot():
        with owner.connect() as connection:
            return (connection.exec_driver_sql("SELECT c.relname,c.oid::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' ORDER BY 1").all(),
                    connection.exec_driver_sql('SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl ORDER BY 1,2').all(),
                    [connection.exec_driver_sql(f'SELECT to_jsonb(t)::text FROM public."{t.name}" t ORDER BY to_jsonb(t)::text').all() for t in metadata.tables.values()],
                    connection.exec_driver_sql("SELECT conname FROM pg_constraint WHERE connamespace='public'::regnamespace AND contype='f' ORDER BY conname").all(),
                    connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one())
    try:
        command.upgrade(configuration(), 'M005')
        with owner.begin() as connection:
            main = create_graph(connection)
            other_image = create_graph(connection, user=main['users'], project=main['projects'], version_no=2)
            other_project = create_graph(connection, user=main['users'])
        before = snapshot()
        assert before[3] == []
        # This intentionally broken last relation must not leave the first 80 installed.
        orphan = {'run_id':uuid4(), 'criteria_item_id':uuid4(), 'result_id':uuid4(), 'project_id':uuid4(),
                  'asset_version_id':uuid4(), 'criteria_version_id':uuid4(), 'selection_kind':'import_projection'}
        with owner.begin() as connection:
            insert_row(connection, 'run_item_selections', orphan)
        with_orphan = snapshot()
        with pytest.raises(CommandError, match='SQLSTATE=23503'):
            command.upgrade(configuration(), 'M006')
        assert snapshot() == with_orphan
        with owner.begin() as connection:
            table=metadata.tables['public.run_item_selections']
            connection.execute(delete(table).where(row_where(table, orphan)))
        assert snapshot() == before
        directory = tmp_path_factory.mktemp('c06_failed_relations')/'migrations'
        shutil.copytree('backend/migrations', directory, ignore=shutil.ignore_patterns('__pycache__'))
        revision=directory/'versions/M006_relations.py'
        revision.write_text(revision.read_text().replace('\n\ndef downgrade():', '\n    op.get_bind().exec_driver_sql("SELECT 1 / 0")\n\ndef downgrade():'))
        with pytest.raises(CommandError, match='SQLSTATE=22012'):
            command.upgrade(configuration(directory),'M006')
        assert snapshot() == before
        command.upgrade(configuration(), 'M006')
        after=snapshot()
        assert after[:2] == before[:2]
        # Tool version changes; business table rows must be byte-identical JSON projections.
        assert after[2] == before[2]
        assert len(after[3]) == 81
        yield database, owner, configuration, main, other_image, other_project
    finally:
        owner.dispose()


def test_final_inventory_all_81_columns_policies_and_validation_flags(relations_database):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import inspect
    from backend.app.persistence.schema import metadata
    _, owner, _, _, _, _ = relations_database
    with owner.connect() as connection:
        inspector=inspect(connection)
        actual=[]
        for table in metadata.tables.values():
            for fk in inspector.get_foreign_keys(table.name,schema='public'):
                actual.append((table.name,fk['name'],tuple(fk['constrained_columns']),fk['referred_table'],tuple(fk['referred_columns'])))
                assert fk['referred_schema']=='public'
                assert fk['options']['ondelete']==fk['options']['onupdate']=='RESTRICT'
            assert not [i for i in inspector.get_indexes(table.name,schema='public') if not i.get('duplicates_constraint')]
        assert sorted(actual)==sorted(FK_CONTRACT) and len(actual)==81
        flags=connection.exec_driver_sql("SELECT convalidated,condeferrable,condeferred,confmatchtype,confdeltype,confupdtype FROM pg_constraint WHERE connamespace='public'::regnamespace AND contype='f'").all()
        assert len(flags)==81 and all(tuple(r)==(True,False,False,'s','r','r') for r in flags)
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one()=='M006'
        # Alembic aliases default schema public to None during reflection.
        from backend.tests.schema_helpers import metadata_with_default_schema_alias
        diff=compare_metadata(MigrationContext.configure(connection,opts={'include_schemas':True,'compare_server_default':True,
            'include_object':lambda obj,name,kind,reflected,compare_to:getattr(obj,'schema','public') in ('public',None) and (kind!='table' or name!='alembic_version')}),metadata_with_default_schema_alias())
        assert diff==[],diff


@pytest.mark.parametrize('source,name,columns,target,references',FK_CONTRACT,ids=[f[1] for f in FK_CONTRACT])
def test_every_fk_path_rejects_orphan_reference(relations_database,request,source,name,columns,target,references):
    from sqlalchemy.exc import DBAPIError
    from backend.app.persistence.schema import metadata
    _,owner,_,main,_,_=relations_database
    row=main['result_evidence'] if name=='fk_evidence_records_item_result' else main['active_run'] if name=='fk_review_runs_active_attempt' else main[source]
    table=metadata.tables['public.'+source]
    patch={columns[-1]:uuid4()}
    if name=='fk_image_coordinate_frames_parent_frame':
        patch['transform_jsonb']={'method':'fixture-identity','matrix':[[1,0,0],[0,1,0],[0,0,1]]}
    if name=='fk_review_runs_retry_of_run':
        patch['request_operation']='retry_review'
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as caught:
            connection.execute(table.update().where(row_where(table,row)).values(**patch))
        assert caught.value.orig.sqlstate=='23503'
        assert caught.value.orig.diag.constraint_name in {f[1] for f in FK_CONTRACT}
        # Redundant composite/incoming FK may reject first. Retain the exact observed name.
        request.node.user_properties.extend([('requested_fk',name),('reported_fk',caught.value.orig.diag.constraint_name)])


CROSS_CONTEXT = [
    ('assets','parent_asset_id','assets','asset_id','project'),
    ('asset_versions','asset_id','assets','asset_id','project'),
    ('asset_versions','original_file_id','stored_files','file_id','project'),
    ('image_coordinate_frames','file_id','stored_files','file_id','project'),
    ('record_aliases','asset_version_id','asset_versions','asset_version_id','project'),
    ('ocr_runs','frame_id','image_coordinate_frames','frame_id','image'),
    ('ocr_runs','attempt_id','review_attempts','attempt_id','image'),
    ('ocr_tokens','ocr_run_id','ocr_runs','ocr_run_id','image'),
    ('poster_extractions','ocr_run_id','ocr_runs','ocr_run_id','image'),
    ('poster_field_observations','extraction_id','poster_extractions','extraction_id','image'),
    ('criteria_drafts','criteria_set_id','criteria_sets','criteria_set_id','project'),
    ('criteria_versions','source_draft_id','criteria_drafts','draft_id','image'),
    ('criteria_items','criteria_version_id','criteria_versions','criteria_version_id','image'),
    ('review_runs','criteria_version_id','criteria_versions','criteria_version_id','image'),
    ('review_attempts','run_id','review_runs','run_id','image'),
    ('item_results','criteria_item_id','criteria_items','criteria_item_id','image'),
    ('item_results','ocr_run_id','ocr_runs','ocr_run_id','image'),
    ('evidence_records','frame_id','image_coordinate_frames','frame_id','image'),
    ('evidence_records','source_ocr_run_id','ocr_runs','ocr_run_id','image'),
    ('evidence_ocr_tokens','token_id','ocr_tokens','token_id','image'),
    ('run_item_selections','result_id','item_results','result_id','image'),
    ('run_item_selections','result_id','item_results','result_id','project'),
]


@pytest.mark.parametrize('source,column,target,key,scope',CROSS_CONTEXT)
def test_existing_ids_from_wrong_project_or_image_are_rejected(relations_database,source,column,target,key,scope):
    from sqlalchemy.exc import DBAPIError
    from backend.app.persistence.schema import metadata
    _,owner,_,main,other_image,other_project=relations_database
    foreign=other_project if scope=='project' else other_image
    row=main['active_attempt'] if source=='review_attempts' else main[source]
    table=metadata.tables['public.'+source]
    patch={column:foreign[target][key]}
    # Avoid a pre-existing target's unrelated UNIQUE key masking the FK rejection.
    if source=='asset_versions' and column=='asset_id':
        patch['version_no']=999
    if source=='ocr_tokens' and column=='ocr_run_id':
        patch.update(token_order=999,external_token_id='c06-invalid-context')
    if source=='poster_field_observations' and column=='extraction_id':
        patch['field_path']='/c06-invalid-context'
    if source=='criteria_items' and column=='criteria_version_id':
        patch.update(position=999,external_item_id='c06-invalid-context')
    if source=='review_attempts' and column=='run_id':
        patch['attempt_no']=999
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as caught:
            connection.execute(table.update().where(row_where(table,row)).values(**patch))
        assert caught.value.orig.sqlstate=='23503'


@pytest.mark.parametrize('part',('token','evidence'))
def test_same_image_different_ocr_cannot_be_mixed_in_evidence_link(relations_database,part):
    from sqlalchemy.exc import DBAPIError
    from backend.app.persistence.schema import metadata
    _,owner,_,main,_,_=relations_database
    with owner_rollback(owner) as connection:
        ocr=insert_row(connection,'ocr_runs',{**main['ocr_runs'],'ocr_run_id':uuid4()})
        token=insert_row(connection,'ocr_tokens',{**main['ocr_tokens'],'token_id':uuid4(),'ocr_run_id':ocr['ocr_run_id']})
        evidence=insert_row(connection,'evidence_records',{**main['evidence_records'],'evidence_id':uuid4(),'source_ocr_run_id':ocr['ocr_run_id']})
        table=metadata.tables['public.evidence_ocr_tokens']
        patch={'token_id':token['token_id']} if part=='token' else {'evidence_id':evidence['evidence_id']}
        with pytest.raises(DBAPIError) as caught:
            connection.execute(table.update().where(row_where(table,main['evidence_ocr_tokens'])).values(**patch))
        assert caught.value.orig.sqlstate=='23503'


@pytest.mark.parametrize('name',('users','projects','stored_files','asset_versions','criteria_versions','review_attempts','ocr_tokens','item_results'))
@pytest.mark.parametrize('operation',('delete','update_key'))
def test_referenced_parent_delete_and_key_change_are_restricted(relations_database,name,operation):
    from sqlalchemy.exc import DBAPIError
    from backend.app.persistence.schema import metadata
    _,owner,_,main,_,_=relations_database
    table=metadata.tables['public.'+name];row=main[name]
    statement=table.delete().where(row_where(table,row)) if operation=='delete' else table.update().where(row_where(table,row)).values(**{list(table.primary_key.columns)[0].name:uuid4()})
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as caught:connection.execute(statement)
        assert caught.value.orig.sqlstate=='23503'


def test_global_schema_file_exception_optional_nulls_and_post_fk_valid_graph(relations_database):
    from backend.app.persistence.schema import metadata
    _,owner,_,main,_,other_project=relations_database
    with owner_rollback(owner) as connection:
        # Deliberate global schema exception: only file identity, not a project-composite FK.
        table=metadata.tables['public.schema_definitions']
        connection.execute(table.update().where(row_where(table,main['schema_definitions'])).values(
            validation_schema_file_id=other_project['files']['validation_schema']['file_id']))
        table=metadata.tables['public.record_aliases']
        connection.execute(table.update().where(row_where(table,main['record_aliases'])).values(import_package_id=None))
        table=metadata.tables['public.criteria_items']
        connection.execute(table.update().where(row_where(table,main['criteria_items'])).values(source_asset_version_id=None))
        # All 81 constraints remain enabled while both insertion cycles are resolved.
        graph=create_graph(connection,user=main['users'])
        assert graph['review_runs']['selected_attempt_id']==graph['review_attempts']['attempt_id']
        assert graph['active_run']['active_attempt_id']==graph['active_attempt']['attempt_id']
        assert connection.exec_driver_sql("SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND contype='f' AND convalidated AND NOT condeferrable").scalar_one()==81


def test_m006_repeat_downgrade_guard_and_app_privileges_stay_closed(relations_database):
    from alembic import command
    from alembic.util import CommandError
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from backend.app.persistence.schema import metadata
    db,owner,configuration,_,_,_=relations_database
    command.upgrade(configuration(),'M006')
    with pytest.raises(CommandError,match='M006 destructive downgrade is disabled'):
        command.downgrade(configuration(),'M005')
    with owner.connect() as connection:
        for table in metadata.tables.values():
            assert not connection.execute(text("SELECT has_table_privilege(:app,:table,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"),{'app':db.settings.database.username,'table':'public.'+table.name}).scalar_one()
    with db.engine.connect() as connection:
        with pytest.raises(DBAPIError) as caught:
            connection.exec_driver_sql('SELECT * FROM public.review_runs')
        assert caught.value.orig.sqlstate=='42501'


@pytest.mark.parametrize('case',('selected_attempt','result_attempt','selection_item'))
def test_same_image_wrong_run_or_item_cannot_be_connected(relations_database,case):
    from sqlalchemy.exc import DBAPIError
    from backend.app.persistence.schema import metadata
    _,owner,_,main,_,_=relations_database
    with owner_rollback(owner) as connection:
        if case=='selected_attempt':
            table=metadata.tables['public.review_runs']
            statement=table.update().where(row_where(table,main['review_runs'])).values(selected_attempt_id=main['active_attempt']['attempt_id'])
        elif case=='result_attempt':
            table=metadata.tables['public.item_results']
            statement=table.update().where(row_where(table,main['item_results'])).values(attempt_id=main['active_attempt']['attempt_id'])
        else:
            item=insert_row(connection,'criteria_items',{**main['criteria_items'],'criteria_item_id':uuid4(),'position':2,'external_item_id':'two'})
            table=metadata.tables['public.run_item_selections']
            statement=table.insert().values(**{**main['run_item_selections'],'criteria_item_id':item['criteria_item_id']})
        with pytest.raises(DBAPIError) as caught:connection.execute(statement)
        assert caught.value.orig.sqlstate=='23503'


@pytest.mark.parametrize('name,column',(('ocr_tokens','project_id'),('evidence_ocr_tokens','asset_version_id'),
                                        ('item_results','criteria_version_id'),('run_item_selections','project_id')))
def test_context_null_cannot_bypass_composite_foreign_keys(relations_database,name,column):
    from sqlalchemy.exc import DBAPIError
    from backend.app.persistence.schema import metadata
    _,owner,_,main,_,_=relations_database
    table=metadata.tables['public.'+name]
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as caught:
            connection.execute(table.update().where(row_where(table,main[name])).values(**{column:None}))
        assert caught.value.orig.sqlstate=='23502'
