"""Same state-dict names and image path as the validated local experiment."""
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
import hashlib
import re

import numpy as np
import torch
from torch import nn
import torch.nn.functional as F
from PIL import Image, ImageOps
from torchvision.models import resnet18
from torchvision.transforms import ColorJitter
from benchlib import LABELS, contained, read


def image(root, row, augment=False):
    with Image.open(contained(root, row['image'])) as source:
        rgb = ImageOps.exif_transpose(source).convert('RGB')
        if augment:
            rgb = ColorJitter(brightness=.08, contrast=.08, saturation=.05, hue=0)(rgb)
        scale = min(448/rgb.width, 448/rgb.height)
        size = (max(1, round(rgb.width*scale)), max(1, round(rgb.height*scale)))
        canvas = Image.new('RGB', (448, 448), (128, 128, 128))
        canvas.paste(rgb.resize(size, Image.Resampling.LANCZOS), ((448-size[0])//2, (448-size[1])//2))
        return canvas


def move(value, device):
    if isinstance(value, torch.Tensor): return value.to(device).contiguous()
    if isinstance(value, Mapping): return {k: move(v, device) for k, v in value.items()}
    if isinstance(value, list): return [move(v, device) for v in value]
    if isinstance(value, tuple): return tuple(move(v, device) for v in value)
    return value


def load_st(model_dir, device):
    from sentence_transformers import SentenceTransformer
    st = SentenceTransformer(str(model_dir), device=str(device), local_files_only=True, trust_remote_code=False,
                             model_kwargs={'torch_dtype': torch.float32}, config_kwargs={'audio_config': None},
                             processor_kwargs={'image_seq_length': 280})
    st.max_seq_length = 8192
    if sum(p.numel() for p in st.parameters()) != 438760448:
        raise ValueError('Unexpected EG2 active text/vision parameter count')
    if st[0].processor.image_processor.max_soft_tokens != 280:
        raise ValueError('Image token budget changed')
    return st


class EG(nn.Module):
    def __init__(self, model_dir, device, heads):
        super().__init__()
        self.st = load_st(model_dir, device)
        self.classifier = nn.Linear(768, 8).to(device)
        with torch.no_grad():
            self.classifier.weight.copy_(torch.tensor([heads[k]['coef'] for k in LABELS], device=device))
            self.classifier.bias.copy_(torch.tensor([heads[k]['intercept'] for k in LABELS], device=device))

    def embed(self, im, device):
        images = im if isinstance(im, list) else [im]
        features = move(self.st.preprocess([{'image': value} for value in images], prompt=None,
                        processing_kwargs={'image': {'max_soft_tokens': 280}}), device)
        return F.normalize(self.st(features)['sentence_embedding'], p=2, dim=1)

    def forward(self, im, device):
        return self.classifier(self.embed(im, device))

    def set_training(self, enabled):
        hf = self.st[0].model
        if enabled:
            hf.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
            self.train()
        else:
            hf.gradient_checkpointing_disable(); self.eval()


class ResNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.backbone = resnet18(weights=None); self.backbone.fc = nn.Identity()
        self.classifier = nn.Linear(512, 8)

    def embed(self, im, device):
        array = np.asarray(im, dtype=np.float32)/255
        x = torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0).contiguous()
        mean = torch.tensor([.485, .456, .406]).view(1, 3, 1, 1)
        std = torch.tensor([.229, .224, .225]).view(1, 3, 1, 1)
        x = ((x-mean)/std).to(device).contiguous()
        return F.normalize(self.backbone(x), p=2, dim=1)

    def forward(self, im, device):
        return self.classifier(self.embed(im, device))


def precision_context(precision, device):
    return torch.autocast(device_type='cuda', dtype=torch.bfloat16) if precision == 'bf16' else nullcontext()


def masked_bce(logits, raw, group_known):
    if group_known <= 0: raise ValueError('Known-label denominator must be positive')
    mask = torch.isfinite(raw)
    safe = torch.nan_to_num(raw, nan=0.)
    numerator = (F.binary_cross_entropy_with_logits(logits.float(), safe.float(), reduction='none') * mask).sum()
    return numerator/group_known, numerator


def representative_parameters(model):
    """Every vision/text block plus bridge/projection/head and used embedding rows."""
    result = {}
    for n, p in model.named_parameters():
        if '.vision_tower.' in n and re.search(r'\.layers\.\d+\.self_attn\.q_proj(?:\.linear)?\.weight$', n): result[n] = p
        elif '.language_model.layers.' in n and n.endswith('.self_attn.q_proj.weight'): result[n] = p
        elif n.endswith(('embedding_projection.weight', '.language_model.norm.weight')) or n.startswith('classifier.'): result[n] = p
    vision = sum('.vision_tower.' in n for n in result)
    text = sum('.language_model.layers.' in n for n in result)
    if (vision, text) != (16, 24): raise ValueError('Expected16 vision/24 text representatives')
    return result


def parameter_hashes(model):
    result = {}
    for n, p in model.named_parameters():
        array = p.detach().float().cpu().contiguous().numpy()
        result[n] = hashlib.sha256(memoryview(array).cast('B')).hexdigest()
    return result
