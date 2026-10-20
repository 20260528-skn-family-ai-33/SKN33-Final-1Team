"""C14 source/plan/path contracts without DB writes or external tooling."""
from pathlib import Path
from uuid import uuid4
import json,os
import pytest
from backend.app.management.io import Tree,OpsError,strict_json,source_tree
from backend.app.management.backup import PgTools
from backend.app.storage.local import LocalStorage,StorageError
from backend.app.hashing.canonical import digest


@pytest.mark.parametrize('raw',[b'{"a":1,"a":2}',b'{"v":NaN}',b'{"v":9007199254740993}',b'{"v":0.12345678901234567890123}',b'{"v":"\\u0000"}',b'{"v":"\\ud800"}'])
def test_unsafe_source_json_never_becomes_a_normal_snapshot(raw):
    with pytest.raises(OpsError):strict_json(raw)


def test_source_decimal_roundtrip_and_raw_bytes_are_distinct_facts():
    assert strict_json(b'{"v":0.10}')=={'v':.1}


@pytest.mark.parametrize('kind',('symlink','hardlink','directory_symlink','case_collision','nfc_collision','file_limit','byte_limit'))
def test_source_tree_rejects_unsafe_or_ambiguous_inventory(tmp_path,kind,monkeypatch):
    source=tmp_path/'source';source.mkdir();(source/'one').write_bytes(b'1')
    if kind=='symlink':(source/'two').symlink_to(tmp_path/'outside')
    if kind=='hardlink':os.link(source/'one',source/'two')
    if kind=='directory_symlink':(source/'dir').symlink_to(tmp_path,target_is_directory=True)
    if kind=='case_collision':(source/'ONE').write_bytes(b'2')
    if kind=='nfc_collision':(source/'é').write_bytes(b'1');(source/'e\u0301').write_bytes(b'2')
    # Default macOS volumes fold these names before Python sees directory entries.
    # Inject the two-name inventory there; case-sensitive Linux exercises real entries.
    if kind in ('case_collision','nfc_collision'):
        expected=['one','ONE'] if kind=='case_collision' else ['one','é','e\u0301']
        if len(os.listdir(source))!=len(expected):
            real=os.listdir;inode=source.stat().st_ino
            monkeypatch.setattr(os,'listdir',lambda fd:expected if isinstance(fd,int) and os.fstat(fd).st_ino==inode else real(fd))
    with Tree(source) as tree:
        with pytest.raises(OpsError):tree.inventory(max_files=0 if kind=='file_limit' else 500,max_bytes=0 if kind=='byte_limit' else 512*1024*1024)


def test_source_allowed_root_and_frozen_digest_are_explicit(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'b').write_bytes(b'b');(source/'a').write_bytes(b'a')
    with source_tree(source,tmp_path) as tree:items=tree.inventory()
    assert [item['relative_path'] for item in items]==['a','b']
    with pytest.raises(OpsError):source_tree(tmp_path,tmp_path/'source')
    envelope=dict(source_namespace='synthetic',target_project_id=str(uuid4()),source_files=items,projection_profile={'version':'v1'})
    first=digest('import_package',envelope,'cc-import-envelope-v1')
    assert first==digest('import_package',json.loads(json.dumps(envelope)),'cc-import-envelope-v1')
    envelope['projection_profile']['version']='v2';assert first!=digest('import_package',envelope,'cc-import-envelope-v1')


def test_private_output_never_overwrites_or_accepts_public_permissions(tmp_path):
    path=tmp_path/'private'
    with Tree(path,create=True,private=True) as tree:
        tree.write('test.json',b'{}')
        with pytest.raises(FileExistsError):tree.write('test.json',b'changed')
    with pytest.raises(FileExistsError):Tree(path,create=True)
    path.chmod(0o755)
    with pytest.raises(OpsError):Tree(path,private=True)


def test_restore_hold_prevents_runtime_start_until_explicit_release(tmp_path):
    path=tmp_path.resolve()/'files';LocalStorage.initialize(path);storage=LocalStorage(path)
    try:
        storage.write_new('restore-hold.json',b'{}')
        with pytest.raises(StorageError,match='RESTORE_VALIDATION_REQUIRED'):storage.start(shared=True)
    finally:storage.close()


@pytest.mark.parametrize('values',[{}, {'PG_TOOLS_MODE':'docker'}, {'PG_TOOLS_MODE':'docker','PG_TOOLS_IMAGE':'postgres:latest'}, {'PG_TOOLS_MODE':'native','PG_TOOLS_BIN':'relative'}])
def test_backup_requires_explicit_versioned_tools(values):
    from types import SimpleNamespace
    settings=SimpleNamespace(host='127.0.0.1',port=5432)
    with pytest.raises(OpsError):PgTools(settings,values)


def test_existing_stage9_hash_vectors_remain_exact():
    data=json.loads(Path('docs/DB_Settings/contracts/stage9-storage-fixtures.json').read_text())
    vectors=next(value for value in data.values() if isinstance(value,list) and value and isinstance(value[0],dict) and 'canonical' in value[0])
    import hashlib
    from backend.app.hashing.canonical import canonical
    for vector in vectors:
        raw=canonical(vector['input']);assert raw.decode()==vector['canonical'] and hashlib.sha256(raw).hexdigest()==vector['sha256']


@pytest.mark.parametrize('kind',('predicate','function','privileges'))
def test_restore_rendering_repair_rejects_every_unrelated_schema_change(monkeypatch,kind):
    from copy import deepcopy
    from types import SimpleNamespace
    from backend.app.management import backup
    from backend.app.persistence.schema_inventory import _load_contract
    contract=_load_contract();actual=deepcopy(contract['structure']);privileges=deepcopy(contract['privileges'])
    if kind=='predicate':next(value for value in actual['constraints'] if value[2]=='c')[3]='CHECK (true)'
    if kind=='function':actual['functions'][0][-1]=not actual['functions'][0][-1]
    if kind=='privileges':privileges['boundary'][1]=True
    monkeypatch.setattr(backup,'capture_structure',lambda _:actual);monkeypatch.setattr(backup,'capture_privileges',lambda *_:privileges)
    with pytest.raises(OpsError,match='RESTORE_SCHEMA_DIFFERENCE'):backup.restore_check_rendering(SimpleNamespace(),'synthetic_app')
