"""CPU-only native-tile and poster-label regression tests."""
from pathlib import Path
import importlib.util
import json
import tempfile
from contextlib import ExitStack, redirect_stdout
import io
import sys
import types
import unittest
from unittest.mock import patch
import numpy as np
import torch
from PIL import Image

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('run_v4',HERE.parent/'highres_v1'/'run.py')
base=importlib.util.module_from_spec(spec);sys.modules['run_v4']=base;spec.loader.exec_module(base)
import run_tiles_v1 as tiles

class TileTests(unittest.TestCase):
    def test_all_original_pixels_are_covered_even_at_edges(self):
        for width,height in [(1,1),(767,768),(768,768),(769,769),(1536,1701),(1603,1809)]:
            coverage=np.zeros((height,width),dtype=np.uint8)
            for x0,y0,x1,y1 in tiles.boxes(width,height):
                self.assertGreater(x1,x0);self.assertGreater(y1,y0)
                self.assertLessEqual(x1-x0,768);self.assertLessEqual(y1-y0,768)
                coverage[y0:y1,x0:x1]+=1
            self.assertTrue(np.all(coverage>0),(width,height))

    def test_native_crop_keeps_pixels_without_resizing(self):
        image=Image.new('RGB',(6,3),(10,20,30));image.putpixel((5,2),(230,50,40))
        piece=tiles.crop_native(image,(2,0,6,3),tile=4)
        self.assertEqual(piece.size,(4,4));self.assertEqual(piece.getpixel((3,2)),(230,50,40))
        self.assertEqual(piece.getpixel((3,3)),(128,128,128))

    def test_two_pass_poster_gradient_matches_all_tile_gradient(self):
        torch.manual_seed(11)
        source=torch.randn((9,8))
        global_a=torch.randn((1,8),requires_grad=True); global_b=global_a.detach().clone().requires_grad_(True)
        scores_a=source.clone().requires_grad_(True);scores_b=source.clone().requires_grad_(True)
        targets=base.target_tensor({'labels':{k:(None if i==1 else i%2) for i,k in enumerate(base.LABELS)}},'cpu')
        out_a=tiles.bag_logits(global_a,9,lambda i:scores_a[i:i+1],training=True)
        out_b=(1-tiles.ALPHA)*global_b+tiles.ALPHA*scores_b.max(dim=0,keepdim=True).values
        loss_a,_=base.masked_loss(out_a,targets);loss_b,_=base.masked_loss(out_b,targets)
        loss_a.backward();loss_b.backward()
        self.assertTrue(torch.allclose(out_a,out_b))
        self.assertTrue(torch.allclose(global_a.grad,global_b.grad))
        self.assertTrue(torch.allclose(scores_a.grad,scores_b.grad))
        self.assertEqual(scores_a.grad[:,1].abs().sum().item(),0.)

    def test_poster_level_forward_covers_every_tile(self):
        image=Image.new('RGB',(1300,760),(255,255,255))
        row={'image_id':'synthetic','labels':dict.fromkeys(base.LABELS,0)}
        body=torch.nn.Linear(4,4);head=torch.nn.Linear(4,8)
        count=[]
        def fake_image(*args):return torch.ones((1,4))
        def fake_tile(im,box,*args):count.append(box);return torch.ones((1,4))*((box[0]+1)/1000)
        def fake_logits(model,layer,x,size):return layer(model(x))
        with patch.object(tiles,'open_original',return_value=image), patch.object(base,'images',side_effect=fake_image), \
             patch.object(tiles,'tile_input',side_effect=fake_tile),patch.object(base,'logits',side_effect=fake_logits):
            out,n=tiles.poster_logits(None,row,body,head,None,None,'cpu',training=True)
            loss,_=base.masked_loss(out,base.target_tensor(row,'cpu'));loss.backward()
        self.assertEqual(n,2)
        self.assertEqual(set(count),set(tiles.boxes(1300,760)))
        self.assertIsNotNone(head.weight.grad)

    def test_poster_level_epoch_updates_only_the_new_model(self):
        body=torch.nn.Linear(4,4);head=torch.nn.Linear(4,8)
        initial=head.weight.detach().clone()
        labels={k:(None if i==1 else i%2) for i,k in enumerate(base.LABELS)}
        row={'image_id':'synthetic','split':'train','use':'active','labels':labels}
        optimizer=torch.optim.AdamW(list(body.parameters())+list(head.parameters()),lr=.01)
        def fake_image(*args):return torch.ones((1,4))
        def fake_tile(im,box,*args):return torch.ones((1,4))*((box[0]+1)/1000)
        def fake_logits(model,layer,x,size):return layer(model(x))
        with patch.object(tiles,'open_original',side_effect=lambda *a:Image.new('RGB',(1300,760))), \
             patch.object(base,'images',side_effect=fake_image),patch.object(tiles,'tile_input',side_effect=fake_tile), \
             patch.object(base,'logits',side_effect=fake_logits):
            result=tiles.train_epoch(None,body,head,[row],optimizer,None,None,1,device='cpu')
        self.assertEqual(result['tiles'],2);self.assertEqual(result['known_cells'],7)
        self.assertTrue(np.isfinite(result['train_loss']))
        self.assertFalse(torch.equal(initial,head.weight))
        with self.assertRaises(ValueError):
            tiles.train_epoch(None,body,head,[{**row,'split':'test'}],optimizer,None,None,1,device='cpu')

    def test_selection_and_evaluation_keep_reference_and_splits_separate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in ('packet','cache','model','reference'):(root/name).mkdir()
            (root/'cache'/'cache.json').write_text('{}')
            reference=root/'reference'/'original.safetensors';reference.write_bytes(b'original')
            row=lambda split:dict(image_id=split,split=split,use='active',labels=dict.fromkeys(base.LABELS,1))
            rows=[row('train'),row('dev'),row('test')]
            identity={'checkpoint':str(reference),'thresholds':[.5]*8}
            env={'packet_sha256':'synthetic-packet','model':identity}
            args=types.SimpleNamespace(packet=root/'packet',cache=root/'cache',model_dir=root/'model',
                                       reference=root/'reference',output=root/'new-run',epochs=1,patience=1)
            body=torch.nn.Linear(4,4);head=torch.nn.Linear(4,8)
            fake_safe=types.ModuleType('safetensors');fake_safe.__path__=[]
            fake_safe_torch=types.ModuleType('safetensors.torch')
            fake_safe_torch.save_file=lambda state,path:Path(path).write_bytes(b'tuned')
            seen=[]
            def baseline(*args):
                seen.append(('baseline',[r['split'] for r in args[3]]));return .6,[[.2]*8]
            def fitted(*args):
                self.assertEqual([r['split'] for r in args[3]],['train'])
                return dict(train_loss=.2,known_cells=8,tiles=2)
            with ExitStack() as stack:
                stack.enter_context(patch.dict(sys.modules,{'safetensors':fake_safe,'safetensors.torch':fake_safe_torch}))
                stack.enter_context(patch.object(tiles,'inputs',return_value=(env,rows)))
                stack.enter_context(patch.object(tiles,'normalizer',return_value=(None,None)))
                stack.enter_context(patch.object(base,'load_models',return_value=(body,head)))
                stack.enter_context(patch.object(base,'freeze_tail'))
                stack.enter_context(patch.object(base,'model_identity',return_value=identity))
                stack.enter_context(patch.object(base,'infer',side_effect=baseline))
                tiles_predict=stack.enter_context(patch.object(tiles,'predict_tiles',side_effect=[(.7,[[.2]*8],2),(.1,[[.8]*8],2),(.1,[[.8]*8],2)]))
                stack.enter_context(patch.object(tiles,'train_epoch',side_effect=fitted))
                stack.enter_context(patch.object(torch.cuda,'manual_seed_all'))
                stack.enter_context(patch.object(torch.cuda,'empty_cache'))
                with redirect_stdout(io.StringIO()):tiles.train(args)
                selected=json.loads((args.output/'selection.json').read_text())
                self.assertFalse(selected['test_used_for_selection']);self.assertEqual(selected['selected']['kind'],'tile')
                self.assertEqual((args.output/'selected.safetensors').read_bytes(),b'tuned')
                self.assertEqual(reference.read_bytes(),b'original')
                with self.assertRaises(ValueError):tiles.train(args)
                with redirect_stdout(io.StringIO()):tiles.evaluate(args)
                result=json.loads((args.output/'evaluation.json').read_text())
                self.assertEqual(result['results']['selected']['macro_f1'],1.)
                self.assertEqual(seen,[('baseline',['dev']),('baseline',['test'])])
                self.assertEqual([r['split'] for r in tiles_predict.call_args.args[3]],['test'])
                self.assertEqual(reference.read_bytes(),b'original')
                with self.assertRaises(ValueError):tiles.evaluate(args)

if __name__=='__main__':unittest.main()
