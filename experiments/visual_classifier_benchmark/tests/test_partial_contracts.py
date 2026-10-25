"""Portable human-approval contracts; Python standard library only."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_partial_data as data


class PartialContractTests(unittest.TestCase):
    def example(self):
        original = dict(image_id='P1', group_id='G1', image='data/images/P1.png', split='train', labels={k: 0 for k in data.LABELS})
        original['labels']['animal'] = None
        row = dict(image_id='P1', group_id='G1', image=original['image'],
                   **{'original_' + k: '?' if v is None else str(v) for k, v in original['labels'].items()},
                   **{'new_' + k: '' for k in data.LABELS}, approval='UNREVIEWED', reviewer='', reason='')
        return original, row

    def test_unreviewed_unchanged_rows_keep_existing_approved_labels(self):
        original, row = self.example()
        corrected, audit = data.corrections([row], [original])
        self.assertEqual(corrected, {}); self.assertEqual(audit, [])

    def test_approved_values_keep_unknown_or_explicitly_change_known_cells(self):
        original, row = self.example()
        row.update(approval='APPROVED', reviewer='human', reason='사람 확인', new_person='1', new_food_drink='?')
        corrected, audit = data.corrections([row], [original])
        self.assertEqual(corrected['P1']['person'], 1)
        self.assertIsNone(corrected['P1']['food_drink']); self.assertIsNone(corrected['P1']['animal'])
        self.assertEqual(len(audit[0]['changes']), 2)
        self.assertEqual(original['labels']['person'], 0)

    def test_missing_reviewer_or_unapproved_change_fails(self):
        original, row = self.example(); row['new_person'] = '1'
        with self.assertRaisesRegex(ValueError, 'Unapproved'):
            data.corrections([row], [original])
        row['approval'] = 'APPROVED'
        with self.assertRaisesRegex(ValueError, 'reviewer and reason'):
            data.corrections([row], [original])

    def test_dev_test_ids_duplicates_and_original_label_tamper_fail(self):
        original, row = self.example()
        with self.assertRaisesRegex(ValueError, 'TRAIN image'):
            data.corrections([row], [dict(original, split='test')])
        with self.assertRaisesRegex(ValueError, 'unique TRAIN'):
            data.corrections([row, row], [original])
        row['original_person'] = '1'
        with self.assertRaisesRegex(ValueError, 'Original label changed'):
            data.corrections([row], [original])

    def test_csv_missing_columns_and_invalid_labels_fail(self):
        for value in ('True', 'false', '2', '-1', 'NaN', '0.0'):
            with self.assertRaises(ValueError):
                data.cell(value)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'bad.csv'; p.write_text('image_id,approval\nP1\n')
            with self.assertRaisesRegex(ValueError, 'header'):
                data.csv_read(p, data.CORRECTION_FIELDS)

    def test_import_does_not_load_model_or_image_packages(self):
        command = "import sys; import prepare_partial_data; assert not set(('torch','numpy','transformers','PIL')) & set(sys.modules)"
        result = subprocess.run([sys.executable, '-S', '-B', '-c', command], cwd=Path(data.__file__).parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
