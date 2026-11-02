"""Approval, split and duplicate guards; synthetic images, no model imports."""
from pathlib import Path
from unittest import mock
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_partial_data as data
from result_fixture import fixture


def image(path, seed, size=(24, 32)):
    from PIL import Image
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes('RGB', size, rng.randbytes(size[0] * size[1] * 3)).save(path)


class PartialDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runs, self.bundle = fixture(self.root)
        self.info = data.read(self.bundle / 'data/dataset_info.json')
        self.ids = data.read(self.bundle / 'data/split_ids.json')
        self.rows = data.read(self.bundle / 'data/train_dev.json')
        images = self.rows + data.read(self.bundle / 'data/test.json')
        for index, row in enumerate(images):
            image(self.bundle / row['image'], index)
        manifest = data.read(self.bundle / 'bundle_manifest.json')
        manifest['files'] = {p.relative_to(self.bundle).as_posix(): dict(bytes=p.stat().st_size, sha256=data.sha(p)) for p in self.bundle.rglob('*') if p.is_file() and p.name != 'bundle_manifest.json'}
        data.save(self.bundle / 'bundle_manifest.json', manifest)
        self.loader = lambda unused: (self.info, self.ids, self.rows)
        self.review = self.root / 'review'
        data.make_review(self.bundle, self.review, loader=self.loader)

    def approve_correction(self):
        rows = data.csv_read(self.review / 'train_corrections.csv', data.CORRECTION_FIELDS)
        row = rows[0]
        old = row['original_person']; row['new_person'] = '0' if old == '1' else '1'
        row.update(approval='APPROVED', reviewer='human-test', reason='사람 검토로 라벨 수정')
        data.csv_write(self.review / 'train_corrections.csv', data.CORRECTION_FIELDS, rows)
        return row

    def add(self, path='new_images/new.png', group='new-group', split='train', approval='APPROVED'):
        row = dict(image_path=path, group_id=group, source_id='new-original-poster', split=split,
                   **{k: ('?' if k == 'fireworks' else '1' if k == 'musical_instrument' else '0') for k in data.LABELS},
                   approval=approval, reviewer='human-test', reason='새 원본을 사람이 검토')
        data.csv_write(self.review / 'additional_labels.csv', data.NEW_FIELDS, [row])
        return row

    def test_review_templates_cover_train_and_dev_but_no_test_gallery(self):
        self.assertEqual(len(data.csv_read(self.review / 'train_corrections.csv', data.CORRECTION_FIELDS)), 113)
        self.assertEqual(data.csv_read(self.review / 'additional_labels.csv', data.NEW_FIELDS), [])
        self.assertFalse((self.review / 'test_review.html').exists())
        self.assertEqual(data.read(self.review / 'review_context.json')['test_images_displayed'], 0)
        self.assertTrue(all(r['approval'] == 'UNREVIEWED' for r in data.csv_read(self.review / 'train_corrections.csv', data.CORRECTION_FIELDS)))

    def test_existing_run_dev_predictions_are_verified_and_used_without_inference(self):
        prior = self.runs / 'siglip-full-fp32'
        protocol = data.read(prior / 'protocol.json')
        protocol['config']['bundle_sha256'] = data.sha(self.bundle / 'bundle_manifest.json')
        protocol['signature'] = data.digest(protocol['config']); data.save(prior / 'protocol.json', protocol)
        for name in ('status.json', 'selection_frozen.json'):
            record = data.read(prior / name); record['protocol_signature'] = protocol['signature']; data.save(prior / name, record)
        data.save(prior / 'selection_commit.json', dict(sha256=data.sha(prior / 'selection_frozen.json')))
        out = self.root / 'review-with-dev'
        data.make_review(self.bundle, out, prior, self.loader)
        self.assertTrue(data.read(out / 'review_context.json')['dev_predictions_loaded'])
        diagnostic = data.csv_read(out / 'dev_diagnostics.csv', ['image_id', 'label', 'truth', 'probability', 'threshold', 'error', 'split'])
        self.assertGreater(len(diagnostic), 0)
        self.assertTrue(all(r['split'] == 'dev' for r in diagnostic))

    def test_wrong_prior_data_fails_before_creating_review_directory(self):
        out = self.root / 'bad-review'
        with self.assertRaisesRegex(ValueError, 'different data'):
            data.make_review(self.bundle, out, self.runs / 'siglip-full-fp32', self.loader)
        self.assertFalse(out.exists())

    def test_approved_changes_and_unknown_labels_survive_immutable_packet(self):
        approved = self.approve_correction()
        image(self.review / 'new_images/new.png', 999)
        self.add()
        before = data.sha(self.bundle / 'data/train_dev.json')
        # Test truth labels must not be parsed during preparation.
        original_read = data.read
        def no_test_labels(path):
            if Path(path).name == 'test.json':
                raise AssertionError('held-out labels must stay closed')
            return original_read(path)
        with mock.patch.object(data, 'read', side_effect=no_test_labels):
            result = data.build_packet(self.bundle, self.review, self.root / 'packet', self.loader)
        self.assertEqual(result['added_images'], 1); self.assertEqual(result['changed_train_cells'], 1)
        manifest, rows = data.validate_packet(self.root / 'packet', self.bundle, self.loader)
        self.assertEqual(len(rows), 151)
        new = next(r for r in rows if r['image_id'] == 'A00001')
        self.assertIsNone(new['labels']['fireworks']); self.assertEqual(new['labels']['musical_instrument'], 1)
        self.assertEqual(next(r for r in rows if r['image_id'] == approved['image_id'])['labels']['person'], int(approved['new_person']))
        self.assertEqual(before, data.sha(self.bundle / 'data/train_dev.json'))
        self.assertFalse(any(r['split'] == 'test' for r in rows))

    def test_unapproved_edit_and_protected_split_are_rejected(self):
        rows = data.csv_read(self.review / 'train_corrections.csv', data.CORRECTION_FIELDS)
        rows[0]['new_person'] = '1'
        data.csv_write(self.review / 'train_corrections.csv', data.CORRECTION_FIELDS, rows)
        with self.assertRaisesRegex(ValueError, 'Unapproved correction'):
            data.build_packet(self.bundle, self.review, self.root / 'packet', self.loader)
        rows[0]['new_person'] = ''; rows[0]['image_id'] = next(r['image_id'] for r in self.rows if r['split'] == 'dev')
        data.csv_write(self.review / 'train_corrections.csv', data.CORRECTION_FIELDS, rows)
        with self.assertRaisesRegex(ValueError, 'TRAIN image'):
            data.build_packet(self.bundle, self.review, self.root / 'packet', self.loader)

    def test_additions_need_approval_and_cannot_use_dev_test_groups(self):
        image(self.review / 'new_images/new.png', 999)
        self.add(approval='UNREVIEWED')
        with self.assertRaisesRegex(ValueError, 'APPROVED'):
            data.build_packet(self.bundle, self.review, self.root / 'packet', self.loader)
        for split, group in (('dev', 'new-group'), ('train', next(r['group_id'] for r in self.ids if r['split'] == 'test'))):
            self.add(group=group, split=split)
            with self.assertRaises(ValueError):
                data.build_packet(self.bundle, self.review, self.root / 'packet', self.loader)

    def test_copied_or_resized_heldout_poster_is_rejected(self):
        from PIL import Image
        protected = next(name for name in data.read(self.bundle / 'bundle_manifest.json')['files'] if name.startswith('data/images/test'))
        target = self.review / 'new_images/new.png'; target.parent.mkdir()
        shutil.copyfile(self.bundle / protected, target); self.add()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            data.build_packet(self.bundle, self.review, self.root / 'packet', self.loader)
        with Image.open(self.bundle / protected) as source:
            source.resize((48, 64), Image.Resampling.LANCZOS).save(target)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            data.build_packet(self.bundle, self.review, self.root / 'packet', self.loader)

    def test_rehashed_unapproved_packet_labels_are_rejected(self):
        image(self.review / 'new_images/new.png', 999); self.add()
        packet = self.root / 'packet'
        data.build_packet(self.bundle, self.review, packet, self.loader)
        rows = data.read(packet / 'train_dev.json'); rows[-1]['labels']['musical_instrument'] = 0
        data.save(packet / 'train_dev.json', rows)
        manifest = data.read(packet / 'packet_manifest.json')
        p = packet / 'train_dev.json'; manifest['files']['train_dev.json'] = dict(bytes=p.stat().st_size, sha256=data.sha(p))
        data.save(packet / 'packet_manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'Unapproved additional label'):
            data.validate_packet(packet, self.bundle, self.loader)

    def test_existing_packet_is_never_overwritten(self):
        packet = self.root / 'packet'; packet.mkdir(); (packet / 'keep').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'separate new'):
            data.build_packet(self.bundle, self.review, packet, self.loader)
        self.assertEqual((packet / 'keep').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
