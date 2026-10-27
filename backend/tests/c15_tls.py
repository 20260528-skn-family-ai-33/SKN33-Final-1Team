"""Group access for a newly generated, test-owned TLS key only."""
import os
import stat


def prepare_test_tls(tls):
    directory = tls.lstat()
    key = tls / 'key.pem'
    private = key.lstat()
    assert stat.S_ISDIR(directory.st_mode) and stat.S_ISREG(private.st_mode), 'C15 TLS paths must not be symlinks.'
    assert directory.st_uid == private.st_uid == os.getuid(), 'C15 TLS must be owned by this test runner.'
    assert directory.st_gid == private.st_gid, 'C15 TLS directory and key must share their actual group.'
    assert stat.S_IMODE(private.st_mode) == 0o600, 'C15 requires a newly generated private key.'
    # No chown, fixed runner UID/GID, world-readable key or additional capability.
    key.chmod(0o640)
    tls.chmod(0o750)
    return str(private.st_gid)
