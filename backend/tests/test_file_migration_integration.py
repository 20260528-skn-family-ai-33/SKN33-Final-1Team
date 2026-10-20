"""M001 -> M002 migration and file-domain constraints on isolated PostgreSQL."""
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_identity_migration_integration import migrated, owner_rollback  # noqa: F401

pytestmark = pytest.mark.db_integration
FILE_NAMES=("assets","asset_versions","stored_files","image_coordinate_frames","record_aliases")
KEYS=dict(zip(FILE_NAMES,("asset_id","asset_version_id","file_id","frame_id","alias_id")))


@pytest.fixture(scope="module")
def files_database(migrated,tmp_path_factory):
    from alembic import command
    from alembic.util import CommandError
    from sqlalchemy import text
    database,owner,configuration=migrated
    user,project=uuid4(),uuid4()
    with owner.begin() as connection:
        connection.execute(text("INSERT INTO public.users (user_id,account_kind,display_name) VALUES (:id,'developer','C03 test fixture')"),{"id":user})
        connection.execute(text("INSERT INTO public.projects (project_id,created_by,name) VALUES (:id,:user,'C03 preserved fixture')"),{"id":project,"user":user})
    def original_snapshot():
        with owner.connect() as connection:
            return (connection.exec_driver_sql("SELECT c.relname,c.oid::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' ORDER BY 1").all(),
                    connection.exec_driver_sql("SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl ORDER BY 1,2").all())
    before=original_snapshot()
    with owner.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE public.stored_files (marker text)")
        connection.exec_driver_sql("INSERT INTO public.stored_files VALUES ('preserve existing object')")
    with pytest.raises(CommandError,match="SQLSTATE=42P07"):
        command.upgrade(configuration(),"M002")
    with owner.begin() as connection:
        assert connection.exec_driver_sql("SELECT marker FROM public.stored_files").scalar_one()=="preserve existing object"
        assert connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one()=="M001"
        assert connection.exec_driver_sql("SELECT to_regclass('public.assets') IS NULL").scalar_one()
        connection.exec_driver_sql("DROP TABLE public.stored_files")
    assert original_snapshot()==before
    directory=tmp_path_factory.mktemp("c03_failed_revision")/"migrations"
    shutil.copytree("backend/migrations",directory,ignore=shutil.ignore_patterns("__pycache__"))
    revision=directory/"versions/M002_files.py"
    revision.write_text(revision.read_text().replace('\n\ndef downgrade():','\n    op.execute(text("SELECT 1 / 0"))\n\ndef downgrade():'))
    with pytest.raises(CommandError,match="SQLSTATE=22012"):
        command.upgrade(configuration(directory),"M002")
    assert original_snapshot()==before
    with owner.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one()=="M001"
        assert connection.execute(text("SELECT count(*) FROM public.users WHERE user_id=:user"),{"user":user}).scalar_one()==1
    command.upgrade(configuration(),"M002")
    yield database,owner,configuration,user,project


def record(name,user,project):
    key={KEYS[name]:uuid4(),"project_id":project}
    if name=="assets":return {**key,"created_by":user,"name":"same asset name"}
    if name=="asset_versions":return {**key,"asset_id":uuid4(),"version_no":1,"original_file_id":uuid4(),"display_file_id":uuid4(),"original_sha256":"a"*64,"original_width":100,"original_height":150,"display_width":100,"display_height":150,"created_by":user}
    if name=="stored_files":return {**key,"file_role":"source_json","storage_key":"objects/"+uuid4().hex,"byte_size":0,"sha256":"b"*64}
    if name=="image_coordinate_frames":return {**key,"asset_version_id":uuid4(),"file_id":uuid4(),"frame_kind":"display","coordinate_space":"normalized_original_pixels","width":100,"height":150}
    return {**key,"asset_version_id":uuid4(),"namespace":"legacy-fixture","external_record_id":uuid4().hex}


def insert(connection,name,body):
    from backend.app.persistence.schema import metadata
    return connection.execute(metadata.tables["public."+name].insert().values(**body))


def test_full_inventory_metadata_and_existing_data_are_preserved(files_database):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import CheckConstraint,inspect,text
    from backend.tests.schema_helpers import metadata_before_relations
    metadata = metadata_before_relations(['users', 'user_sessions', 'projects', 'project_members', 'assets', 'asset_versions', 'stored_files', 'image_coordinate_frames', 'record_aliases'])
    _,owner,_,user,project=files_database
    with owner.connect() as connection:
        inspector=inspect(connection)
        assert set(inspector.get_table_names(schema="public"))=={t.name for t in metadata.tables.values()}|{"alembic_version"}
        assert sum(len(inspector.get_columns(t.name,schema="public")) for t in metadata.tables.values())==88
        assert connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one()=="M002"
        differences=compare_metadata(MigrationContext.configure(connection,opts={"include_schemas":True,"compare_server_default":True,
            "include_object":lambda obj,name,kind,reflected,compare_to:getattr(obj,"schema","public") in ("public",None) and (kind!="table" or name!="alembic_version")}),metadata)
        assert differences==[],differences
        for name in FILE_NAMES:
            table=metadata.tables["public."+name]
            assert connection.exec_driver_sql(f'SELECT count(*) FROM public."{name}"').scalar_one()==0
            assert inspector.get_pk_constraint(name,schema="public")["name"]==table.primary_key.name
            assert {x["name"] for x in inspector.get_check_constraints(name,schema="public")}=={c.name for c in table.constraints if isinstance(c,CheckConstraint)}
            assert inspector.get_table_comment(name,schema="public")["text"]==table.comment
            assert all(c["comment"] for c in inspector.get_columns(name,schema="public"))
            assert not inspector.get_foreign_keys(name,schema="public")
            assert not [i for i in inspector.get_indexes(name,schema="public") if not i.get("duplicates_constraint")]
        assert connection.execute(text("SELECT name FROM public.projects WHERE project_id=:id"),{"id":project}).scalar_one()=="C03 preserved fixture"
        assert connection.execute(text("SELECT display_name FROM public.users WHERE user_id=:id"),{"id":user}).scalar_one()=="C03 test fixture"
        assert connection.exec_driver_sql("SELECT i.indnullsnotdistinct FROM pg_constraint c JOIN pg_index i ON i.indexrelid=c.conindid WHERE c.conname='uq_record_aliases_external_record'").scalar_one()


@pytest.mark.parametrize("name,changes,expected",[
    ("assets",{"name":" \t"},"domain"),("assets",{"asset_kind":"other"},"domain"),
    ("assets",{"parent_asset_id":"@self"},"domain"),("assets",{"created_at":"infinity"},"times"),
    ("assets",{"updated_at":"2000-01-01T00:00:00Z"},"times"),
    ("asset_versions",{"version_no":0},"domain"),("asset_versions",{"original_width":0},"domain"),
    ("asset_versions",{"original_height":-1},"domain"),("asset_versions",{"display_width":0},"domain"),
    ("asset_versions",{"display_height":0},"domain"),("asset_versions",{"original_sha256":"A"*64},"hashes"),
    ("asset_versions",{"original_sha256":"a"*63},"hashes"),("asset_versions",{"measurement_metadata":[]},"json_shapes"),
    ("asset_versions",{"measurement_metadata":None},"json_shapes"),("asset_versions",{"verification_state":"verified"},"domain"),
    ("asset_versions",{"created_at":"infinity"},"times"),("asset_versions",{"original_file_id":None},"23502"),
    ("stored_files",{"file_role":"other"},"domain"),("stored_files",{"storage_backend":"s3"},"domain"),
    ("stored_files",{"storage_key":" \t"},"domain"),("stored_files",{"byte_size":-1},"domain"),
    ("stored_files",{"file_role":"original_image","detected_mime":"image/png"},"domain"),
    ("stored_files",{"file_role":"display_image","byte_size":1},"domain"),
    ("stored_files",{"file_role":"ocr_input","byte_size":1,"detected_mime":" "},"domain"),
    ("stored_files",{"sha256":"B"*64},"hashes"),("stored_files",{"state":"deleted"},"domain"),
    ("stored_files",{"parent_file_id":"@self"},"domain"),("stored_files",{"source_metadata":[]},"json_shapes"),
    ("stored_files",{"source_metadata":None},"json_shapes"),("stored_files",{"source_metadata":"@sql_null"},"23502"),
    ("stored_files",{"updated_at":"2000-01-01T00:00:00Z"},"times"),("stored_files",{"created_at":"infinity"},"times"),
    ("stored_files",{"storage_key":"x"*513},"22001"),
    ("image_coordinate_frames",{"frame_kind":"other"},"domain"),("image_coordinate_frames",{"coordinate_space":" "},"domain"),
    ("image_coordinate_frames",{"width":0},"domain"),("image_coordinate_frames",{"height":-1},"domain"),
    ("image_coordinate_frames",{"verification_state":"verified"},"domain"),
    ("image_coordinate_frames",{"parent_frame_id":"@self","transform_jsonb":{}},"domain"),
    ("image_coordinate_frames",{"parent_frame_id":"@parent"},"domain"),
    ("image_coordinate_frames",{"transform_jsonb":{}},"domain"),
    ("image_coordinate_frames",{"parent_frame_id":"@parent","transform_jsonb":[]},"json_shapes"),
    ("image_coordinate_frames",{"parent_frame_id":"@parent","transform_jsonb":"@json_null"},"json_shapes"),
    ("image_coordinate_frames",{"parent_frame_id":"@parent","transform_jsonb":1},"json_shapes"),
    ("image_coordinate_frames",{"created_at":"infinity"},"times"),
    ("record_aliases",{"namespace":" "},"domain"),("record_aliases",{"external_record_id":"\t"},"domain"),
    ("record_aliases",{"created_at":"infinity"},"times"),("record_aliases",{"namespace":"x"*101},"22001"),
    ("record_aliases",{"external_record_id":"x"*201},"22001"),
])
def test_file_checks_reject_invalid_rows(files_database,name,changes,expected):
    from sqlalchemy import JSON,null
    from sqlalchemy.exc import DBAPIError
    _,owner,_,user,project=files_database
    body=record(name,user,project)
    for key,value in changes.items():
        if isinstance(value,str) and value.startswith("@"):
            value={"@self":body[KEYS[name]],"@parent":uuid4(),"@json_null":JSON.NULL,"@sql_null":null()}[value]
        body[key]=value
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as caught:insert(connection,name,body)
        assert caught.value.orig.sqlstate==(expected if expected.isdigit() else "23514")
        if not expected.isdigit():assert caught.value.orig.diag.constraint_name=="ck_"+name+"_"+expected


@pytest.mark.parametrize("name",FILE_NAMES)
def test_primary_keys_are_unique(files_database,name):
    from sqlalchemy.exc import IntegrityError
    _,owner,_,user,project=files_database
    with owner_rollback(owner) as connection:
        body=record(name,user,project);insert(connection,name,body)
        with pytest.raises(IntegrityError) as caught:insert(connection,name,body)
        assert caught.value.orig.sqlstate=="23505"


@pytest.mark.parametrize("kind",["asset_version","storage_location","alias_without_package","alias_with_package"])
def test_semantic_unique_keys(files_database,kind):
    from sqlalchemy.exc import IntegrityError
    _,owner,_,user,project=files_database
    name={"asset_version":"asset_versions","storage_location":"stored_files","alias_without_package":"record_aliases","alias_with_package":"record_aliases"}[kind]
    body=record(name,user,project)
    if kind=="alias_with_package":body["import_package_id"]=uuid4()
    duplicate={**body,KEYS[name]:uuid4()}
    if name=="record_aliases":duplicate["asset_version_id"]=uuid4()
    if name=="stored_files":duplicate["project_id"]=uuid4()  # key location uniqueness is global
    with owner_rollback(owner) as connection:
        insert(connection,name,body)
        with pytest.raises(IntegrityError) as caught:insert(connection,name,duplicate)
        assert caught.value.orig.sqlstate=="23505"


def test_valid_file_roles_defaults_bigint_and_duplicate_hashes(files_database):
    from sqlalchemy import select
    from backend.app.persistence.schema import metadata
    _,owner,_,user,project=files_database
    table=metadata.tables["public.stored_files"]
    roles=("original_image","display_image","ocr_input","source_json","import_manifest","schema_specification","validation_schema")
    with owner_rollback(owner) as connection:
        for role in roles:
            body=record("stored_files",user,project);body["file_role"]=role
            if role in roles[:3]:body.update(byte_size=3_000_000_000,detected_mime="image/png")
            insert(connection,"stored_files",body)
        rows=connection.execute(select(table.c.state,table.c.storage_backend,table.c.source_metadata,table.c.byte_size)).all()
        assert len(rows)==7 and all(r[0:3]==("stored","local",{}) for r in rows)
        assert sum(r[3]>2_147_483_647 for r in rows)==3
        # All seven rows use the same SHA256 and remain separate records.


def test_valid_frame_parent_transform_nulls_and_multiple_frames(files_database):
    from sqlalchemy import select
    from backend.app.persistence.schema import metadata
    _,owner,_,user,project=files_database
    table=metadata.tables["public.image_coordinate_frames"]
    with owner_rollback(owner) as connection:
        parent=record("image_coordinate_frames",user,project);parent["transform_jsonb"]=None
        insert(connection,"image_coordinate_frames",parent)
        child={**parent,"frame_id":uuid4(),"parent_frame_id":parent["frame_id"],"frame_kind":"ocr_input",
               "transform_jsonb":{"matrix":[[0.5,0,0],[0,0.5,0],[0,0,1]],"method":"scale","version":1}}
        insert(connection,"image_coordinate_frames",child)
        insert(connection,"image_coordinate_frames",{**parent,"frame_id":uuid4()})
        assert connection.execute(select(table.c.transform_jsonb.is_(None)).where(table.c.frame_id==parent["frame_id"])).scalar_one()
        assert len(connection.execute(select(table.c.frame_id)).all())==3


def test_alias_scope_and_original_identifiers_are_preserved(files_database):
    from sqlalchemy import select
    from backend.app.persistence.schema import metadata
    _,owner,_,user,project=files_database
    table=metadata.tables["public.record_aliases"]
    with owner_rollback(owner) as connection:
        body=record("record_aliases",user,project);body.update(namespace=" raw namespace ",external_record_id=" Raw:Poster/01 ")
        insert(connection,"record_aliases",body)
        for patch in ({"import_package_id":uuid4()},{"import_package_id":uuid4()},{"project_id":uuid4()},{"namespace":"other"}):
            insert(connection,"record_aliases",{**body,"alias_id":uuid4(),**patch})
        rows=connection.execute(select(table.c.external_record_id)).scalars().all()
        assert rows==[" Raw:Poster/01 "]*5


def test_app_privileges_and_repeat_upgrade_downgrade_guard(files_database):
    from alembic import command
    from alembic.util import CommandError
    from sqlalchemy import text
    database,owner,configuration,_,_=files_database
    command.upgrade(configuration(),"M002")
    with pytest.raises(CommandError,match="M002 destructive downgrade is disabled"):
        command.downgrade(configuration(),"M001")
    with owner.connect() as connection:
        for name in FILE_NAMES:
            assert not connection.execute(text("SELECT has_table_privilege(:app,:table,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"),
                {"app":database.settings.database.username,"table":"public."+name}).scalar_one()
        assert connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one()=="M002"
