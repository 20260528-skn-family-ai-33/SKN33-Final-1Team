"""HF snapshot symlinks are normal; manifest paths and file hashes stay pinned."""
from pathlib import Path
import hashlib
import json
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_benchmark import get_model_dir


class ModelCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.bundle = self.root / 'bundle'
        (self.bundle / 'references').mkdir(parents=True)
        self.snapshot = self.root / 'cache/models--google--embeddinggemma-2/snapshots/pinned'
        self.snapshot.mkdir(parents=True)
        self.content = b'{"pooling_mode_mean_tokens":true}'
        self.checksum = hashlib.sha256(self.content).hexdigest()
        self.relative = '1_Pooling/config.json'
        self.manifest({self.relative: self.checksum})

    def manifest(self, files):
        (self.bundle / 'references/base_model_files.json').write_text(json.dumps({'files': files}))

    def link_blob(self, content=None):
        blob = self.snapshot.parent.parent / 'blobs' / self.checksum
        blob.parent.mkdir(parents=True)
        blob.write_bytes(self.content if content is None else content)
        link = self.snapshot / self.relative
        link.parent.mkdir(parents=True)
        link.symlink_to(os.path.relpath(blob, link.parent))
        self.assertFalse(link.resolve().is_relative_to(self.snapshot))
        return blob

    def test_downloaded_hub_snapshot_links_to_sibling_blobs(self):
        self.link_blob()
        download = Mock(return_value=str(self.snapshot))
        with patch.dict(sys.modules, {'huggingface_hub': SimpleNamespace(snapshot_download=download)}):
            result = get_model_dir(self.bundle, None, self.root / 'cache')
        self.assertEqual(result, self.snapshot)
        self.assertEqual(download.call_args.kwargs['allow_patterns'], [self.relative])

    def test_explicit_snapshot_accepts_the_same_verified_links(self):
        self.link_blob()
        self.assertEqual(get_model_dir(self.bundle, self.snapshot, None), self.snapshot)

    def test_materialized_model_directory_still_works(self):
        file = self.snapshot / self.relative
        file.parent.mkdir(parents=True)
        file.write_bytes(self.content)
        self.assertEqual(get_model_dir(self.bundle, self.snapshot, None), self.snapshot)

    def test_changed_blob_is_rejected(self):
        self.link_blob(b'changed bytes')
        with self.assertRaisesRegex(ValueError, 'Downloaded model revision/file differs'):
            get_model_dir(self.bundle, self.snapshot, None)

    def test_broken_link_is_rejected(self):
        self.link_blob().unlink()
        with self.assertRaisesRegex(ValueError, 'Downloaded model revision/file differs'):
            get_model_dir(self.bundle, self.snapshot, None)

    def test_manifest_traversal_is_rejected_before_reading(self):
        for name in ('../outside', '/outside', 'a/../../outside', r'..\outside', '', '.'):
            with self.subTest(name=name):
                self.manifest({name: self.checksum})
                with patch('run_benchmark.sha', side_effect=AssertionError('must not read outside the logical manifest path')):
                    with self.assertRaisesRegex(ValueError, 'Invalid path in pinned model manifest'):
                        get_model_dir(self.bundle, self.snapshot, None)


if __name__ == '__main__':
    unittest.main()
