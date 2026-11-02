"""Meaningful CPU checks for masks, split leakage, threshold selection and checkpoints."""
from pathlib import Path
import json
import io
import math
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from benchlib import LABELS,contained,load_test,metrics,read,save,sha,thresholds,timing_orders,validate_rows


class Contracts(unittest.TestCase):
    def test_unknown_is_excluded_from_metrics(self):
        y=np.tile(np.array([[1],[0],[np.nan]],dtype=np.float32),(1,8))
        p=np.tile(np.array([[.9],[.1],[.99]]),(1,8))
        s=metrics(y,p,[.5]*8)
        self.assertEqual(s['known_evaluated_cells'],16)
        self.assertEqual(s['masked_unknown_cells'],8)
        self.assertEqual(s['label_wise_accuracy'],1)
        self.assertEqual(s['macro_f1'],1)
        p[2]=.01
        self.assertEqual(metrics(y,p,[.5]*8),s)

    def test_masked_accumulation_matches_full_batch_gradient(self):
        import torch
        from models import masked_bce
        raw=torch.tensor([[1.,float('nan'),0.],[0.,1.,float('nan')]])
        logits=torch.tensor([[.1,.2,.3],[-.1,-.2,.4]],requires_grad=True)
        loss,_=masked_bce(logits,raw,4);loss.backward();full=logits.grad.clone()
        micro=logits.detach().clone().requires_grad_()
        for i in range(2):
            part,_=masked_bce(micro[i:i+1],raw[i:i+1],4);part.backward()
        torch.testing.assert_close(full,micro.grad)
        self.assertEqual(float(full[0,1]),0)
        self.assertEqual(float(full[1,2]),0)
        self.assertTrue(torch.isfinite(full).all())

    def test_last_group_uses_actual_known_count(self):
        import torch
        from models import masked_bce
        logits=torch.zeros((1,3),requires_grad=True);raw=torch.tensor([[1.,0.,float('nan')]])
        loss,_=masked_bce(logits,raw,2)
        self.assertAlmostEqual(float(loss.detach()),math.log(2),places=6)
        with self.assertRaises(ValueError):masked_bce(logits,raw,0)

    def test_threshold_tie_prefers_nearest_half(self):
        y=np.tile([[1],[0],[np.nan]],(1,8)).astype(float)
        p=np.tile([[.8],[.2],[.5]],(1,8))
        self.assertEqual(thresholds(y,p),[.5]*8)

    def test_selection_hash_checked_before_test_read(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'selection.json';save(p,{'thresholds':[.5]*8})
            with patch('benchlib.read',side_effect=AssertionError('must not parse test')):
                with self.assertRaises(ValueError):load_test(d,p,'0'*64)

    def test_invalid_row_types_and_population_rejected(self):
        ids=[dict(image_id='P001',group_id='G001',split='train')]
        row=dict(image_id='P001',group_id='G001',split='train',labels={k:0 for k in LABELS})
        validate_rows([row],'train',ids)
        row['labels']['person']=False
        with self.assertRaises(ValueError):validate_rows([row],'train',ids)
        with self.assertRaises(ValueError):validate_rows([],'train',ids)

    def test_paths_cannot_escape_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):contained(d,'../outside')

    def test_cuda_required_no_cpu_fallback(self):
        import torch
        from types import SimpleNamespace
        from run_benchmark import cuda_environment
        with patch.object(torch.cuda,'is_available',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'CUDA GPU required'):
                cuda_environment(SimpleNamespace(precision='fp32'))

    def test_timing_same_order_and_all_rows_each_repeat(self):
        a=timing_orders(37,3)
        self.assertEqual(a,timing_orders(37,3));self.assertEqual(len(a),111)
        for repeat in range(3):self.assertEqual(sorted(i for r,i in a if r==repeat),list(range(37)))

    def test_epoch_checkpoint_restores_optimizer_and_model(self):
        import torch
        from run_benchmark import torch_save
        torch.manual_seed(7);model=torch.nn.Linear(2,1);opt=torch.optim.AdamW(model.parameters(),lr=.01)
        x=torch.tensor([[1.,2.]])
        def step(m,o):o.zero_grad();m(x).square().sum().backward();o.step()
        step(model,opt)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'latest.pt';torch_save(p,dict(model=model.state_dict(),optimizer=opt.state_dict(),epoch=1))
            state=torch.load(p,map_location='cpu',weights_only=True)
            other=torch.nn.Linear(2,1);other.load_state_dict(state['model'])
            opt2=torch.optim.AdamW(other.parameters(),lr=.01);opt2.load_state_dict(state['optimizer'])
            step(model,opt);step(other,opt2)
            for a,b in zip(model.parameters(),other.parameters()):torch.testing.assert_close(a,b,rtol=0,atol=0)
            self.assertFalse(p.with_name('latest.pt.tmp').exists())

    def test_existing_run_is_not_modified_without_resume(self):
        import argparse
        import run_benchmark as runner
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'existing';out.mkdir();save(out/'protocol.json',{'sentinel':True})
            bundle=Path(d)/'bundle';bundle.mkdir();save(bundle/'bundle_manifest.json',{})
            # Run rejects before importing torch or creating a new protocol.
            err=io.StringIO()
            with patch('sys.argv',['run_benchmark.py','evaluate','--bundle',str(bundle),'--run-dir',str(out)]),patch.object(runner,'verify_bundle',return_value=({},[],[])),patch('sys.stderr',err):
                self.assertEqual(runner.main(),1)
            self.assertIn('Run exists',err.getvalue())
            self.assertEqual(read(out/'protocol.json'),{'sentinel':True})
            self.assertFalse((out/'failure.json').exists())


if __name__=='__main__':unittest.main()
