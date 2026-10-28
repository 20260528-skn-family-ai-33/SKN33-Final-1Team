"""CPU-only contract checks; no model download, inference or training."""
import importlib.util
import math
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import torch
import run

class ContractTests(unittest.TestCase):
    def test_unknown_mask_has_no_loss_or_gradient(self):
        z=torch.tensor([[0.,0.,0.]],requires_grad=True)
        y=torch.tensor([[1.,float('nan'),0.]])
        loss,n=run.masked_loss(z,y);loss.backward()
        self.assertEqual(n,2);self.assertAlmostEqual(loss.item(),2*math.log(2),places=5)
        self.assertEqual(z.grad[0,1].item(),0.0)

    def test_metrics_ignore_unknown_and_count_support(self):
        labels=dict.fromkeys(run.LABELS,0);labels['food_drink']=None;labels['person']=1
        row={'labels':labels}
        prob=[[0.9,0.9,0.1,0.1,0.1,0.1,0.1,0.1]]
        m=run.score([row],prob,[.5]*8)
        self.assertEqual(m['known_cells'],7);self.assertEqual(m['accuracy'],1.)
        self.assertEqual(m['per_label'][1]['known'],0)
        self.assertIsNone(m['per_label'][1]['f1'])

    def test_cache_letterbox_and_source_hash(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);packet=root/'packet';packet.mkdir();(packet/'packet.json').write_text('{}')
            source=packet/'example.png';Image.new('RGB',(40,20),(255,0,0)).save(source)
            row={'image':'example.png','image_id':'X','sha256':run.sha(source)}
            args=type('Args',(),{'packet':packet,'cache':root/'cache'})()
            with patch.object(run,'check_packet',return_value=({},[row],{})):
                run.make_cache(args)
            with Image.open(root/'cache/384/X.png') as result:
                self.assertEqual(result.size,(384,384))
                self.assertEqual(result.getpixel((0,0)),(128,128,128))
                self.assertEqual(result.getpixel((192,192)),(255,0,0))
            run.check_cache(args,[row])
            (root/'cache/384/X.png').write_bytes(b'changed')
            with self.assertRaises(ValueError):run.check_cache(args,[row])

    def test_reference_identity_rejects_modified_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);model=root/'model';ref=root/'ref';model.mkdir();ref.mkdir()
            files={}
            for name in ('config.json','model.safetensors','preprocessor_config.json'):
                path=model/name
                path.write_text(json.dumps({'model_type':'siglip','vision_config':{'image_size':384}}) if name=='config.json' else name)
                files[name]={'bytes':path.stat().st_size,'sha256':run.sha(path)}
            ckpt=ref/'candidates/tail4-lr1e-06/best.safetensors';ckpt.parent.mkdir(parents=True);ckpt.write_bytes(b'checkpoint')
            config={'model_packet':{'files':files}};sig=hashlib.sha256(json.dumps(config,sort_keys=True,allow_nan=False).encode()).hexdigest()
            def write(name,obj): (ref/name).write_text(json.dumps(obj))
            write('protocol.json',{'config':config,'signature':sig})
            write('status.json',{'status':'complete','protocol_signature':sig})
            write('selection_frozen.json',{'protocol_signature':sig,'checkpoint':'candidates/tail4-lr1e-06/best.safetensors','artifact_hashes':{'best.safetensors':run.sha(ckpt)},'thresholds':[.5]*8})
            write('selection_commit.json',{'sha256':run.sha(ref/'selection_frozen.json')})
            self.assertEqual(run.model_identity(model,ref)['checkpoint_sha256'],run.sha(ckpt))
            ckpt.write_bytes(b'changed')
            with self.assertRaises(ValueError):run.model_identity(model,ref)

    def test_safe_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            self.assertEqual(run.inside(root,'images/a.jpg'),(root/'images/a.jpg').resolve())
            with self.assertRaises(ValueError):run.inside(root,'../outside')

if __name__=='__main__':unittest.main()
