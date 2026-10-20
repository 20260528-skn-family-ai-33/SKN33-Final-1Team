"""Bounded descriptor-anchored snapshots. No symlinks, hardlinks or shell expansion."""
from contextlib import contextmanager
from decimal import Decimal
import hashlib,json,math,os,stat,unicodedata
from pathlib import Path
from ..hashing.canonical import canonical,validate_tree
from ..security.errors import AuthError
from ..storage.local import _open_directory


class OpsError(RuntimeError):
    def __init__(self,code):self.code=code;super().__init__(code)


def relative(value):
    try:encoded=value.encode('utf-8') if isinstance(value,str) else b''
    except UnicodeError:raise OpsError('INPUT_PATH_INVALID') from None
    if (not isinstance(value,str) or not value or len(encoded)>1024 or '\\' in value or ':' in value
        or value.startswith('/') or any(part in ('','.','..') for part in value.split('/'))):raise OpsError('INPUT_PATH_INVALID')
    try:validate_tree(value)
    except ValueError:raise OpsError('INPUT_PATH_INVALID') from None
    return value


class Tree:
    def __init__(self,path,*,create=False,private=False):
        self.path=Path(path)
        if create:
            parent=_open_directory(self.path.parent)
            try:os.mkdir(self.path.name,0o700,dir_fd=parent);os.fsync(parent)
            finally:os.close(parent)
        self.fd=_open_directory(self.path)
        info=os.fstat(self.fd)
        if private and (info.st_uid!=os.geteuid() or info.st_mode&0o077):self.close();raise OpsError('PRIVATE_DIRECTORY_REQUIRED')
    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
    @contextmanager
    def directory(self,name='',*,create=False):
        fd=os.dup(self.fd)
        try:
            for part in relative(name).split('/') if name else ():
                if create:
                    try:os.mkdir(part,0o700,dir_fd=fd);os.fsync(fd)
                    except FileExistsError:pass
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=child
            yield fd
        finally:os.close(fd)
    @contextmanager
    def read(self,name):
        name=relative(name);parent,_,leaf=name.rpartition('/')
        with self.directory(parent) as fd:child=os.open(leaf,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        try:
            info=os.fstat(child)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise OpsError('INPUT_FILE_UNSAFE')
            with os.fdopen(child,'rb',closefd=False) as stream:yield stream
        finally:os.close(child)
    def bytes(self,name,limit):
        with self.read(name) as stream:
            value=stream.read(limit+1)
            if len(value)>limit:raise OpsError('INPUT_LIMIT_EXCEEDED')
            return value
    def write(self,name,data):
        parent,_,leaf=relative(name).rpartition('/')
        with self.directory(parent,create=True) as fd:
            child=os.open(leaf,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
            with os.fdopen(child,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.fsync(fd)
    def copy_from(self,name,stream,item):
        parent,_,leaf=relative(name).rpartition('/');hasher=hashlib.sha256();size=0
        with self.directory(parent,create=True) as fd:
            child=os.open(leaf,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
            with os.fdopen(child,'wb') as output:
                while block:=stream.read(1024*1024):
                    size+=len(block)
                    if size>int(item['byte_size']):raise OpsError('INPUT_CHANGED')
                    output.write(block);hasher.update(block)
                if size!=int(item['byte_size']) or hasher.hexdigest()!=item['sha256']:raise OpsError('INPUT_CHANGED')
                output.flush();os.fsync(output.fileno())
            os.fsync(fd)
    def inventory(self,*,max_files=500,max_bytes=512*1024*1024,exclude=(),max_path_bytes=255):
        result=[];total=0;seen=set()
        def visit(prefix):
            nonlocal total
            with self.directory(prefix) as fd:
                names=os.listdir(fd)
                if len(names)>max_files+100:raise OpsError('INPUT_LIMIT_EXCEEDED')
                for name in names:
                    path=relative(prefix+'/'+name if prefix else name)
                    if len(path.encode('utf-8'))>max_path_bytes:raise OpsError('INPUT_PATH_INVALID')
                    if path in exclude:continue
                    folded=unicodedata.normalize('NFC',path).casefold()
                    if folded in seen:raise OpsError('INPUT_NAME_COLLISION')
                    seen.add(folded);info=os.stat(name,dir_fd=fd,follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):visit(path)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink==1:
                        if len(result)>=max_files or total+info.st_size>max_bytes:raise OpsError('INPUT_LIMIT_EXCEEDED')
                        with self.read(path) as stream:
                            before=os.fstat(stream.fileno());hash_value=hashlib.file_digest(stream,'sha256').hexdigest();after=os.fstat(stream.fileno())
                            if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise OpsError('INPUT_CHANGED')
                        total+=before.st_size
                        result.append(dict(relative_path=path,byte_size=str(before.st_size),sha256=hash_value))
                    else:raise OpsError('INPUT_FILE_UNSAFE')
        visit('')
        return sorted(result,key=lambda item:item['relative_path'].encode('utf-16-be'))


def source_tree(path,allowed):
    path,allowed=Path(path),Path(allowed)
    if not path.is_absolute() or not allowed.is_absolute() or '..' in path.parts or '..' in allowed.parts:raise OpsError('INPUT_PATH_INVALID')
    try:path.relative_to(allowed)
    except ValueError:raise OpsError('SOURCE_OUTSIDE_ALLOWED_ROOT') from None
    # Traversing every component with O_NOFOLLOW prevents lexical allowlist bypass.
    with Tree(allowed):pass
    return Tree(path)


def strict_json(raw,*,max_bytes=20*1024*1024):
    if len(raw)>max_bytes:raise OpsError('JSON_LIMIT_EXCEEDED')
    def pairs(items):
        value={}
        for name,item in items:
            if name in value:raise ValueError()
            value[name]=item
        return value
    def number(value):
        original=Decimal(value);result=float(original)
        if not math.isfinite(result) or Decimal(str(result))!=original:raise ValueError()
        return result
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_float=number,
                         parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        validate_tree(value);return value
    except (ValueError,UnicodeError,RecursionError):raise OpsError('SOURCE_JSON_UNSUPPORTED') from None


def json_bytes(value):return canonical(value)


def verify_bytes(value,item):
    if len(value)!=int(item['byte_size']) or hashlib.sha256(value).hexdigest()!=item['sha256']:raise OpsError('INPUT_CHANGED')
    return value
