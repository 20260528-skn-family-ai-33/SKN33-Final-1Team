"""Small POSIX fixture-permission tests; no Docker, root, TLS server or DB."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from backend.tests.c15_tls import prepare_test_tls


@unittest.skipUnless(os.name == 'posix', 'C15 fixture permissions require POSIX')
class TestOwnedTLS(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cc-c15-unit-')
        self.addCleanup(self.temporary.cleanup)
        self.tls = Path(self.temporary.name) / 'tls'
        self.tls.mkdir()
        self.key = self.tls / 'key.pem'
        self.key.write_text('test-only placeholder, not a private key')
        self.key.chmod(0o600)

    def test_actual_group_is_selected_without_changing_ownership(self):
        before = self.key.stat()
        group = prepare_test_tls(self.tls)
        after = self.key.stat()
        self.assertEqual(group, str(before.st_gid))
        self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(self.tls.stat().st_mode), 0o750)

    def test_foreign_owner_is_rejected_before_chmod(self):
        with patch('backend.tests.c15_tls.os.getuid', return_value=self.key.stat().st_uid + 1):
            with self.assertRaisesRegex(AssertionError, 'owned by this test runner'):
                prepare_test_tls(self.tls)
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)

    def test_nonprivate_key_is_rejected(self):
        self.key.chmod(0o644)
        with self.assertRaisesRegex(AssertionError, 'newly generated private key'):
            prepare_test_tls(self.tls)
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o644)

    def test_key_symlink_is_rejected_without_touching_target(self):
        target = self.key.with_name('target.pem')
        self.key.rename(target)
        self.key.symlink_to(target)
        with self.assertRaisesRegex(AssertionError, 'must not be symlinks'):
            prepare_test_tls(self.tls)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_directory_symlink_is_rejected_without_touching_target(self):
        link = self.tls.with_name('tls-link')
        link.symlink_to(self.tls, target_is_directory=True)
        with self.assertRaisesRegex(AssertionError, 'must not be symlinks'):
            prepare_test_tls(link)
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)
