"""Optional oracle checks against the unchanged experiment metrics implementation."""
from pathlib import Path
import importlib.util
import random
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import resultlib as lib


@unittest.skipUnless(importlib.util.find_spec('numpy') and importlib.util.find_spec('sklearn'), 'Existing numpy/sklearn runtime unavailable; do not install for this test')
class ResultMetricOracleTests(unittest.TestCase):
    def test_metrics_match_existing_implementation_with_unknowns_and_probability_ties(self):
        import numpy as np
        sys.path.insert(0, str(HERE.parent / 'poster_benchmark'))
        import benchlib
        rng = random.Random(24)
        labels, probabilities = [], []
        for i in range(37):
            labels.append(dict(image_id=str(i), labels={k: (None if rng.random() < .05 else rng.randrange(2)) for k in lib.LABELS}))
            probabilities.append([rng.choice([.125, .25, .5, .75, .875]) for _ in lib.LABELS])
        cuts = [.5, .3, .7, .5, .5, .2, .8, .5]
        actual, _ = lib.score_rows(lib.rows_with_probabilities(labels, probabilities, cuts), cuts)
        y = np.asarray([[np.nan if r['labels'][k] is None else r['labels'][k] for k in lib.LABELS] for r in labels])
        expected = benchlib.metrics(y, np.asarray(probabilities), cuts)
        lib.compare_metrics(expected, actual, 'oracle')
        self.assertAlmostEqual(lib.percentile([1., 2., 4., 9.], .95), float(np.percentile([1., 2., 4., 9.], 95)))

    def test_dev_threshold_policies_match_existing_f1_and_focused_code(self):
        import numpy as np
        sys.path.insert(0, str(HERE.parent / 'poster_benchmark'))
        import benchlib
        # Only import the old focused runner when its already-installed torch is available.
        if importlib.util.find_spec('torch') is None:
            self.skipTest('Existing torch unavailable; no installation')
        import finetune_siglip_focused as focused
        rng = random.Random(7)
        rows = [dict(labels={k: (i + j) % 2 for j, k in enumerate(lib.LABELS)}) for i in range(37)]
        p = [[rng.randrange(1, 20) / 20 for _ in lib.LABELS] for _ in rows]
        y = np.asarray([[r['labels'][k] for k in lib.LABELS] for r in rows])
        self.assertEqual(lib.dev_thresholds(rows, p), benchlib.thresholds(y, np.asarray(p)))
        self.assertEqual(lib.dev_thresholds(rows, p, True), focused.focused_thresholds(y, np.asarray(p)))


if __name__ == '__main__':
    unittest.main()
