"""Offline image encoders. Only the eight per-label heads are trained."""
from contextlib import nullcontext
from pathlib import Path
import json

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from PIL import Image, ImageOps

SPECS = {
    'siglip2': ('siglip2-base-384', 'google/siglip2-base-patch16-384'),
    'convnextv2': ('convnextv2-tiny-384', 'timm/convnextv2_tiny.fcmae_ft_in22k_in1k_384'),
}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def letterbox(path, size):
    # Keep poster edges: center cropping can remove small visual objects.
    with Image.open(path) as source:
        rgb = ImageOps.exif_transpose(source).convert('RGB')
    scale = min(size / rgb.width, size / rgb.height)
    shape = (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale)))
    canvas = Image.new('RGB', (size, size), (128, 128, 128))
    canvas.paste(rgb.resize(shape, Image.Resampling.BICUBIC),
                 ((size - shape[0]) // 2, (size - shape[1]) // 2))
    return canvas


class Encoder(nn.Module):
    def __init__(self, backbone, dimension, mean, std, size, device, precision, kind):
        super().__init__()
        self.backbone = backbone.to(device).eval().requires_grad_(False)
        self.dimension, self.size, self.device = dimension, size, torch.device(device)
        self.precision, self.kind = precision, kind
        self.mean = torch.tensor(mean, dtype=torch.float32).view(1, 3, 1, 1)
        self.std = torch.tensor(std, dtype=torch.float32).view(1, 3, 1, 1)
        if self.mean.shape != (1, 3, 1, 1) or (self.std <= 0).any():
            raise ValueError('Invalid normalization')

    def prepare(self, path):
        with letterbox(path, self.size) as im:
            array = np.asarray(im, dtype=np.float32).copy() / 255.0
        x = torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0).contiguous()
        return ((x - self.mean) / self.std).contiguous()

    def forward(self, x):
        dtype = {'fp16': torch.float16, 'bf16': torch.bfloat16}.get(self.precision)
        context = torch.autocast('cuda', dtype=dtype) if dtype else nullcontext()
        with context:
            output = self.backbone(x)
            if self.kind == 'siglip2':
                output = output.pooler_output
        if output.ndim != 2 or output.shape[1] != self.dimension:
            raise ValueError('Unexpected encoder feature shape: ' + str(output.shape))
        # Head inference always uses FP32, matching the cached feature contract.
        return F.normalize(output.float(), p=2, dim=-1)


def load_encoder(kind, folder, device, precision='fp32', size=384):
    folder = Path(folder)
    config = read_json(folder / 'config.json')
    if not (folder / 'model.safetensors').is_file():
        raise FileNotFoundError('Missing downloaded model.safetensors: ' + str(folder))
    if kind == 'siglip2':
        from transformers import AutoModel
        if config.get('model_type') != 'siglip':
            raise ValueError('Expected the downloaded SigLIP 2 fixed-resolution checkpoint')
        processor = read_json(folder / 'preprocessor_config.json')
        if config.get('vision_config', {}).get('image_size') != size:
            raise ValueError('SigLIP input resolution must match its downloaded config')
        # Load the complete local checkpoint, retain only the vision tower.
        # Text embeddings account for much of the download but are unused here.
        full, loading = AutoModel.from_pretrained(
            str(folder), local_files_only=True, trust_remote_code=False,
            use_safetensors=True, dtype=torch.float32, attn_implementation='sdpa',
            output_loading_info=True)
        if loading.get('missing_keys') or loading.get('mismatched_keys') or loading.get('unexpected_keys'):
            raise ValueError('SigLIP checkpoint keys do not match: ' + str(loading))
        backbone = full.vision_model
        dimension = full.config.vision_config.hidden_size
        del full
        mean, std = processor['image_mean'], processor['image_std']
    elif kind == 'convnextv2':
        import timm
        from safetensors.torch import load_file
        if config.get('architecture') != 'convnextv2_tiny':
            raise ValueError('Expected ConvNeXt V2 Tiny')
        backbone = timm.create_model('convnextv2_tiny', pretrained=False,
                                     num_classes=config['num_classes'])
        backbone.load_state_dict(load_file(str(folder / 'model.safetensors')), strict=True)
        backbone.reset_classifier(0)
        dimension = backbone.num_features
        if dimension != config['num_features']:
            raise ValueError('ConvNeXt feature dimension mismatch')
        mean, std = config['pretrained_cfg']['mean'], config['pretrained_cfg']['std']
    else:
        raise ValueError('Unknown encoder: ' + kind)
    return Encoder(backbone, dimension, mean, std, size, device, precision, kind)


def make_head(selection, device):
    head = nn.Linear(len(selection['coef'][0]), 8, device=device, dtype=torch.float32)
    with torch.no_grad():
        head.weight.copy_(torch.tensor(selection['coef'], device=device, dtype=torch.float32))
        head.bias.copy_(torch.tensor(selection['intercept'], device=device, dtype=torch.float32))
    return head.eval().requires_grad_(False)
