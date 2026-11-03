"""Real JCS vectors and real local file/process behavior, without PostgreSQL."""
from io import BytesIO
from pathlib import Path
import hashlib
import json
import os
from uuid import uuid4
import pytest
from PIL import Image
from pydantic import ValidationError
from backend.app.hashing.canonical import canonical, loads, digest
from backend.app.contracts.business import DraftCreate, DraftPut, ProjectCreate, ProjectPatch, Item
from backend.app.services.cursors import Cursors
from backend.app.config import Secret
from backend.app.storage.local import LocalStorage, StorageError, key, operation_key
from backend.app.storage.images import normalize
from backend.app.security.errors import AuthError

FIXTURES = Path('docs/DB_Settings/contracts')
VECTORS = json.loads((FIXTURES / 'stage8-hash-vectors.json').read_text())
IMPORTS = json.loads((FIXTURES / 'stage9-storage-fixtures.json').read_text())


@pytest.mark.parametrize('case', VECTORS['cases'] + IMPORTS['hash_cases'], ids=lambda case: case['id'])
def test_frozen_vectors_with_real_rfc8785(case):
    # HASH-11 is deliberately outside the API integer profile; JCS numbers are binary64.
    value = json.loads(case['raw_input_json'], parse_int=float) if 'raw_input_json' in case else case['input']
    raw = canonical(value)
    assert raw.decode() == case['canonical']
    assert len(raw) == case['utf8_bytes']
    assert hashlib.sha256(raw).hexdigest() == case['sha256']


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'{"n":NaN}', b'{"n":Infinity}', b'{"n":9007199254740992}',
    b'{"s":"\\u0000"}', b'{"s":"\\ud800"}', b'\xff', b'{', b'['*34+b'0'+b']'*34])
def test_json_rejects_ambiguous_or_unsafe_values(raw):
    with pytest.raises(AuthError): loads(raw)


def test_json_byte_bound_unicode_preservation_and_number_semantics():
    with pytest.raises(AuthError) as error: loads(b' ' * 262145)
    assert error.value.status == 413
    assert digest('x', 'é') != digest('x', 'e\u0301')
    assert canonical({'n': -0.0}) == canonical({'n': 0})


@pytest.mark.parametrize('model,body', [(ProjectCreate, {'name':' '}), (ProjectCreate, {'name':'x','extra':1}),
    (ProjectPatch, {}), (ProjectPatch, {'archived':1}), (ProjectPatch, {'name':None}),
    (DraftPut, {'expected_revision':'9223372036854775808','raw_requirements':'x','items':[],'source_documents':None}),
    (DraftPut, {'expected_revision':1,'raw_requirements':'x','items':[],'source_documents':None}),
    (DraftPut, {'expected_revision':'1','raw_requirements':'x','items':[]}),
    (Item, {'id':'1','text':'','type':'visual','source':'x','selected':1}),
    (Item, {'id':'1','text':'x','type':'visual','source':'x','selected':True,'is_required':True})])
def test_strict_contract_rejections(model, body):
    with pytest.raises(ValidationError): model.model_validate(body)


def test_cursors_bind_context_and_expiry():
    now = [1.0]; cursors = Cursors(Secret('synthetic-cursor-secret-' * 3), clock=lambda: now[0])
    context = {'kind':'images','actor':'one','limit':20}
    token = cursors.issue(context, ['timestamp','id'])
    assert cursors.read(token, context) == ['timestamp','id']
    for changed in (dict(context,actor='two'), dict(context,limit=21), dict(context,kind='projects')):
        with pytest.raises(AuthError): cursors.read(token, changed)
    for bad in (token+'x','forged','x'*2049):
        with pytest.raises(AuthError): cursors.read(bad, context)
    now[0] = 86401
    with pytest.raises(AuthError): cursors.read(token, context)


@pytest.fixture
def storage(tmp_path,posix_storage):
    root = tmp_path.resolve() / 'storage'
    LocalStorage.initialize(root)
    current = LocalStorage(root)
    try: yield current
    finally: current.close()


def intent(data=b'payload'):
    project, file_id, op = map(str, (uuid4(),uuid4(),uuid4()))
    return op, dict(project_id=project,file_id=file_id,storage_key=key(project,file_id),byte_size=str(len(data)),sha256=hashlib.sha256(data).hexdigest())


def test_publish_fsync_verify_no_replacement(storage):
    op, item = intent()
    storage.write_new('staging/'+op+'/'+item['file_id'], b'payload')
    storage.publish(op, item)
    with storage.verified(item['storage_key'],7,item['sha256']) as stream: assert stream.read() == b'payload'
    storage.write_new('staging/'+op+'/'+item['file_id'], b'changed')
    with pytest.raises(StorageError): storage.publish(op,item)
    with storage.verified(item['storage_key'],7,item['sha256']) as stream: assert stream.read() == b'payload'
    assert not (storage.path / 'staging' / op / item['file_id']).is_symlink()


def test_crash_between_link_and_unlink_resumes_only_same_inode(storage):
    op,item=intent();storage.write_new('staging/'+op+'/'+item['file_id'],b'payload')
    parent,_,name=item['storage_key'].rpartition('/')
    with storage.directory(parent,create=True) as target,storage.directory('staging/'+op) as source:
        os.link(item['file_id'],name,src_dir_fd=source,dst_dir_fd=target,follow_symlinks=False)
    storage.publish(op,item)
    with storage.verified(item['storage_key'],7,item['sha256']) as stream: assert os.fstat(stream.fileno()).st_nlink == 1


@pytest.mark.parametrize('kind', ['symlink','hardlink','fifo','tamper','missing'])
def test_unsafe_payloads_and_tampering_are_refused(storage,kind):
    op,item=intent();storage.write_new(item['storage_key'],b'payload')
    path=storage.path/item['storage_key']
    if kind=='hardlink': os.link(path,storage.path/'staging'/'external')
    elif kind=='tamper': path.write_bytes(b'changed')
    else:
        path.unlink()
        if kind=='symlink': path.symlink_to(storage.path/'marker')
        elif kind=='fifo': os.mkfifo(path,0o600)
    with pytest.raises((OSError,StorageError)): storage.verified(item['storage_key'],7,item['sha256'])


def test_directory_symlink_root_replacement_and_initialization_collision(storage,tmp_path):
    with pytest.raises(FileExistsError): LocalStorage.initialize(storage.path)
    os.symlink(storage.path/'staging',storage.path/'objects'/'unsafe')
    with pytest.raises(OSError):
        with storage.directory('objects/unsafe'): pass
    moved=storage.path.with_name('moved');storage.path.rename(moved)
    storage.path.mkdir(mode=0o700)
    try:
        with pytest.raises(StorageError): storage.check_anchor()
    finally:
        storage.path.rmdir();moved.rename(storage.path)


def test_real_process_shared_exclusive_capabilities_and_service_exclusion(storage):
    storage.start()
    result=storage.capabilities()
    assert result['process_shared_exclusive_nonblocking'] and not result['power_failure_certified']
    other=LocalStorage(storage.path)
    try:
        with pytest.raises(Exception): other.start()
    finally: other.close()
    with storage.lock('gate',shared=True):
        with storage.lock('gate',shared=True): pass
        with pytest.raises(StorageError):
            with storage.lock('gate'): pass
    assert operation_key(str(uuid4())) in range(-(2**63),2**63)


def image_bytes(fmt='PNG',size=(3,2),mode='RGBA',**kwargs):
    out=BytesIO();Image.new(mode,size,(255,0,0,0) if mode=='RGBA' else 'red').save(out,format=fmt,**kwargs)
    return out.getvalue()


@pytest.mark.parametrize('fmt', ['PNG','JPEG','WEBP'])
def test_actual_format_and_transparency_normalization(fmt):
    raw=image_bytes(fmt,mode='RGB' if fmt=='JPEG' else 'RGBA')
    display,info=normalize(raw)
    assert info['actual_format']==fmt
    with Image.open(BytesIO(display)) as image:
        assert image.format=='PNG' and image.mode=='RGB' and image.size==(3,2)
        if fmt=='PNG': assert image.getpixel((0,0))==(255,255,255)


def test_exif_transpose_once_and_original_dimensions():
    exif=Image.Exif();exif[274]=6
    raw=image_bytes('JPEG',mode='RGB',exif=exif)
    display,info=normalize(raw)
    assert (info['original_width'],info['original_height'])==(3,2)
    assert (info['display_width'],info['display_height'])==(2,3)
    with Image.open(BytesIO(display)) as image: assert image.getexif().get(274) is None


@pytest.mark.parametrize('raw', [
    pytest.param(b'',id='empty-image'),
    pytest.param(b'not image',id='invalid-image'),
    pytest.param(b'x'*(20*1024*1024+1),id='oversized-image'),
])
def test_invalid_or_oversized_image(raw):
    with pytest.raises(AuthError): normalize(raw)


def test_animated_and_pixel_limits():
    out=BytesIO();Image.new('RGB',(2,2),'red').save(out,format='PNG',save_all=True,append_images=[Image.new('RGB',(2,2),'blue')])
    with pytest.raises(AuthError): normalize(out.getvalue())
    for size in ((8193,1),(4000,3000)):
        with pytest.raises(AuthError): normalize(image_bytes(size=size,mode='RGB'))
