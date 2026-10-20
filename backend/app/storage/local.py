"""Private POSIX storage anchored by directory descriptors; payloads never replace.

Operational journals describe intent, not DB truth. No garbage collection lives here.
"""
from contextlib import contextmanager,ExitStack
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import sys
from uuid import UUID, uuid4
import portalocker
from ..hashing.canonical import canonical, loads

MARKER = b'creative-checker-local-v1\n'


class StorageError(RuntimeError):
    def __init__(self, code='STORAGE_UNAVAILABLE'):
        super().__init__(code)
        self.code = code


def uuid(value):
    parsed = str(UUID(str(value)))
    if parsed != str(value): raise StorageError('STORAGE_PATH_INVALID')
    return parsed


def key(project, file_id):
    return f'objects/projects/{uuid(project)}/files/{uuid(file_id)}/payload.bin'


def operation_key(op):
    return int.from_bytes(hashlib.sha256(('cc-storage-op-v1:' + uuid(op)).encode()).digest()[:8], 'big', signed=True)


def _open_directory(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts: raise StorageError('STORAGE_PATH_INVALID')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


class LocalStorage:
    def __init__(self, root):
        self.path = Path(root)
        self.fd = _open_directory(root)
        self.closed = False
        self.service_fd = None
        try:
            info = os.fstat(self.fd)
            self.device, self.inode = info.st_dev, info.st_ino
            if info.st_uid != os.geteuid() or info.st_mode & 0o077: raise StorageError('STORAGE_ROOT_UNSAFE')
            with self.read('marker') as stream:
                if stream.read(128) != MARKER: raise StorageError('STORAGE_MARKER_INVALID')
        except BaseException:
            self.close()
            raise

    @classmethod
    def initialize(cls, root):
        """Create a new dedicated root only. Refuse every existing destination."""
        path = Path(root)
        parent = _open_directory(path.parent)
        try:
            os.mkdir(path.name, mode=0o700, dir_fd=parent)
            os.fsync(parent)
        finally: os.close(parent)
        fd = _open_directory(path)
        try:
            for name in ('objects', 'staging', 'operations', 'locks'):
                os.mkdir(name, mode=0o700, dir_fd=fd)
            marker = os.open('marker', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try: os.write(marker, MARKER); os.fsync(marker)
            finally: os.close(marker)
            os.fsync(fd)
        finally: os.close(fd)

    def close(self):
        if self.service_fd is not None:
            os.close(self.service_fd); self.service_fd = None
        if not self.closed:
            os.close(self.fd); self.closed = True

    def check_anchor(self):
        fd = _open_directory(self.path)
        try:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (self.device, self.inode): raise StorageError('STORAGE_ROOT_CHANGED')
        finally: os.close(fd)

    @contextmanager
    def directory(self, relative='', *, create=False):
        self.check_anchor()
        parts = relative.split('/') if relative else []
        if any(not part or part in ('.', '..') or '\\' in part for part in parts): raise StorageError('STORAGE_PATH_INVALID')
        fd = os.dup(self.fd)
        try:
            for part in parts:
                if create:
                    try: os.mkdir(part, 0o700, dir_fd=fd); os.fsync(fd)
                    except FileExistsError: pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                info = os.fstat(child)
                if info.st_dev != self.device or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    os.close(child); raise StorageError('STORAGE_DIRECTORY_UNSAFE')
                os.close(fd); fd = child
            yield fd
        finally: os.close(fd)

    def regular(self, fd, *, links=1):
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_dev != self.device or info.st_uid != os.geteuid()
                or info.st_nlink != links or info.st_mode & 0o077):
            raise StorageError('STORAGE_FILE_UNSAFE')
        return info

    @contextmanager
    def read(self, relative, *, links=1):
        parent, _, name = relative.rpartition('/')
        with self.directory(parent) as directory:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            self.regular(fd, links=links)
            with os.fdopen(fd, 'rb', closefd=False) as stream: yield stream
        finally: os.close(fd)

    def write_new(self, relative, data):
        parent, _, name = relative.rpartition('/')
        with self.directory(parent, create=True) as directory:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                self.regular(fd)
                with os.fdopen(fd, 'wb', closefd=False) as stream:
                    stream.write(data); stream.flush(); os.fsync(fd)
            finally: os.close(fd)
            os.fsync(directory)

    def journal(self, value):
        relative = 'operations/' + uuid(value['operation_id'])
        raw = canonical(value)
        temporary = 'journal-' + str(uuid4()) + '.tmp'
        self.write_new(relative + '/' + temporary, raw)
        with self.directory(relative) as directory:
            # Replacement is restricted to operational metadata, never payload bytes.
            try:
                old = os.open('journal.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except FileNotFoundError: old = None
            if old is not None:
                try: self.regular(old)
                finally: os.close(old)
            os.replace(temporary, 'journal.json', src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)

    def journals(self):
        with self.directory('operations') as directory: names = os.listdir(directory)
        for name in sorted(names):
            uuid(name)
            try:
                with self.read('operations/' + name + '/journal.json') as stream: value = loads(stream.read(262145))
            except FileNotFoundError:
                # Crash before an intent is durable: no publication is possible.
                continue
            if (value.get('contract_version') != 'cc-storage-journal-v1' or value.get('operation_id') != name
                    or value.get('operation') not in ('upload_image','review_result','import_package')
                    or (not 1<=len(value.get('files', []))<=502 if value.get('operation')=='import_package'
                        else len(value.get('files', [])) != (2 if value.get('operation')=='upload_image' else 4))):
                raise StorageError('STORAGE_JOURNAL_INVALID')
            uuid(value['project_id']); uuid(value['image_id'])
            if value['operation']=='upload_image': uuid(value['asset_id'])
            elif value['operation']=='review_result':
                for field in ('run_id','attempt_id','ocr_id','extraction_id'): uuid(value[field])
            else:uuid(value['import_id'])
            for item in value['files']:
                if item['storage_key'] != key(value['project_id'], item['file_id']): raise StorageError('STORAGE_JOURNAL_INVALID')
            yield value

    @contextmanager
    def lock(self, name, *, shared=False, nonblocking=True):
        if not re.fullmatch(r'[a-z0-9-]{1,80}', name): raise StorageError('STORAGE_PATH_INVALID')
        with self.directory('locks') as directory:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
            self.regular(fd)
            flags = portalocker.LOCK_SH if shared else portalocker.LOCK_EX
            if nonblocking: flags |= portalocker.LOCK_NB
            try: portalocker.lock(fd, flags)
            except portalocker.exceptions.LockException: raise StorageError('STORAGE_BUSY') from None
            yield fd
        finally: os.close(fd)

    def start(self,*,shared=False,maintenance=False):
        if not maintenance:
            try:
                with self.read('restore-hold.json'):pass
            except FileNotFoundError:pass
            else:raise StorageError('RESTORE_VALIDATION_REQUIRED')
        with self.directory('locks') as directory:
            fd = os.open('service', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
            self.regular(fd)
            portalocker.lock(fd, (portalocker.LOCK_SH if shared else portalocker.LOCK_EX) | portalocker.LOCK_NB)
            self.service_fd = fd
            # Startup probes share names and require isolation between new workers.
            with self.lock('capabilities',nonblocking=False):self.capabilities()
        except BaseException:
            if self.service_fd != fd: os.close(fd)
            raise

    @contextmanager
    def recovery_lock(self,name,*,concurrent=False):
        """Concurrent startup skips a live owner; corruption still fails closed."""
        with ExitStack() as stack:
            try:stack.enter_context(self.lock(name))
            except StorageError as error:
                if not concurrent or error.code!='STORAGE_BUSY':raise
                yield False
                return
            yield True

    def capabilities(self):
        """Real fsync/no-clobber and independent-process shared/exclusive lock probes."""
        name = 'probe-' + str(uuid4())
        self.write_new('staging/' + name, b'probe')
        with self.directory('staging') as directory:
            try:
                os.link(name, name + '-linked', src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                try: os.link(name, name + '-linked', src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                except FileExistsError: pass
                else: raise StorageError('STORAGE_NOCLOBBER_UNSUPPORTED')
                os.fsync(directory)
            finally:
                for candidate in (name, name + '-linked'):
                    try: os.unlink(candidate, dir_fd=directory)
                    except FileNotFoundError: pass
                os.fsync(directory)
        script = '''import os,sys,portalocker
fd=os.open(sys.argv[1],os.O_RDWR|os.O_NOFOLLOW)
try:
 portalocker.lock(fd,int(sys.argv[2])|portalocker.LOCK_NB)
except portalocker.exceptions.LockException: sys.exit(3)
finally: os.close(fd)
'''
        with self.lock('probe', shared=True):
            for flag, expected in ((portalocker.LOCK_SH, 0), (portalocker.LOCK_EX, 3)):
                result = subprocess.run([sys.executable, '-c', script, str(self.path / 'locks/probe'), str(int(flag))],
                                        capture_output=True, timeout=10)
                if result.returncode != expected: raise StorageError('STORAGE_LOCK_UNSUPPORTED')
        with self.lock('probe'):
            for flag in (portalocker.LOCK_SH, portalocker.LOCK_EX):
                result = subprocess.run([sys.executable, '-c', script, str(self.path / 'locks/probe'), str(int(flag))],
                                        capture_output=True, timeout=10)
                if result.returncode != 3: raise StorageError('STORAGE_LOCK_UNSUPPORTED')
        return {'platform': sys.platform, 'file_fsync': True, 'directory_fsync': True, 'no_clobber': True,
                'process_shared_exclusive_nonblocking': True, 'power_failure_certified': False}

    def publish(self, operation, item):
        source = 'staging/' + uuid(operation)
        target = item['storage_key']
        if target != key(item['project_id'], item['file_id']): raise StorageError('STORAGE_PATH_INVALID')
        parent, _, name = target.rpartition('/')
        with self.directory(source) as staging, self.directory(parent, create=True) as final:
            stage_name = uuid(item['file_id'])
            try:
                os.link(stage_name, name, src_dir_fd=staging, dst_dir_fd=final, follow_symlinks=False)
            except FileExistsError:
                left = os.stat(stage_name, dir_fd=staging, follow_symlinks=False)
                right = os.stat(name, dir_fd=final, follow_symlinks=False)
                if (left.st_dev, left.st_ino) != (right.st_dev, right.st_ino) or left.st_nlink != 2:
                    raise StorageError('STORAGE_COLLISION') from None
            with self.read(target, links=2) as stream:
                if os.fstat(stream.fileno()).st_size != int(item['byte_size']) or hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                    raise StorageError('STORAGE_CORRUPT')
            os.fsync(final)
            os.unlink(stage_name, dir_fd=staging); os.fsync(staging)

    def verified(self, storage_key, size, sha256):
        parts = storage_key.split('/')
        if len(parts) != 6 or storage_key != key(parts[2], parts[4]): raise StorageError('STORAGE_PATH_INVALID')
        parent, _, name = storage_key.rpartition('/')
        with self.directory(parent) as directory:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stream = os.fdopen(fd, 'rb')
        try:
            if self.regular(fd).st_size != size or hashlib.file_digest(stream, 'sha256').hexdigest() != sha256:
                raise StorageError('STORAGE_CORRUPT')
            stream.seek(0)
            return stream
        except BaseException: stream.close(); raise
