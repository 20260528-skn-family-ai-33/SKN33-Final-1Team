"""Small trainable additions to an existing SigLIP2 vision checkpoint."""
import copy
import math

import torch
from torch import nn
from torch.nn import functional as F

import finetune_siglip as ft
from benchlib import LABELS, contained

INSTRUMENT = LABELS.index('musical_instrument')
OTHER_LABELS = [i for i in range(len(LABELS)) if i != INSTRUMENT]


def require(condition, message):
    if not condition:
        raise ValueError(message)


class InstrumentEncoder(nn.Module):
    """Keep the global feature intact and add one instrument-specific MAP probe."""
    def __init__(self, base, original_head):
        super().__init__()
        self.base = base.eval().requires_grad_(False)
        self.pooler = copy.deepcopy(base.backbone.head).eval().requires_grad_(False)
        require(hasattr(self.pooler, 'probe'), 'This checkpoint must have the original SigLIP MAP probe')
        self.pooler.probe.requires_grad_(True)
        self.classifier = nn.Linear(base.dimension, 1, device=base.device, dtype=torch.float32)
        with torch.no_grad():
            self.classifier.weight.copy_(original_head.weight[INSTRUMENT:INSTRUMENT + 1])
            self.classifier.bias.copy_(original_head.bias[INSTRUMENT:INSTRUMENT + 1])
        self.dimension = base.dimension + 1

    @property
    def backbone(self):
        return self.base.backbone

    @property
    def device(self):
        return self.base.device

    @property
    def size(self):
        return self.base.size

    @property
    def mean(self):
        return self.base.mean

    @property
    def std(self):
        return self.base.std

    def prepare(self, path):
        return self.base.prepare(path)

    def train(self, mode=True):
        super().train(mode)
        self.base.eval()
        self.pooler.eval()
        self.classifier.train(mode)
        return self

    def forward(self, x):
        # Exactly one vision tower call. No crop, second image encoder or text tower.
        output = self.backbone(x)
        global_feature = F.normalize(output.pooler_output.float(), p=2, dim=-1)
        instrument_feature = F.normalize(self.pooler(output.last_hidden_state).float(), p=2, dim=-1)
        instrument_logit = self.classifier(instrument_feature)
        return torch.cat((global_feature, instrument_logit), dim=-1)

    def extra_state(self):
        return {**{'pooler.' + k: v.detach().cpu().contiguous().clone() for k, v in self.pooler.state_dict().items()},
                **{'classifier.' + k: v.detach().cpu().contiguous().clone() for k, v in self.classifier.state_dict().items()}}

    def load_extra(self, state):
        expected = {'pooler.' + k for k in self.pooler.state_dict()} | {'classifier.' + k for k in self.classifier.state_dict()}
        require(set(state) == expected, 'Instrument probe checkpoint keys differ')
        self.pooler.load_state_dict({k[7:]: v for k, v in state.items() if k.startswith('pooler.')}, strict=True)
        self.classifier.load_state_dict({k[11:]: v for k, v in state.items() if k.startswith('classifier.')}, strict=True)


class InstrumentOutput(nn.Module):
    def __init__(self, original_head):
        super().__init__()
        self.original_head = original_head.eval().requires_grad_(False)
        self.dimension = original_head.in_features

    def train(self, mode=True):
        super().train(mode)
        self.original_head.eval()
        return self

    def forward(self, features):
        logits = self.original_head(features[:, :self.dimension].contiguous()).clone()
        logits[:, INSTRUMENT] = features[:, self.dimension]
        return logits


class LoRALinear(nn.Module):
    """A frozen linear projection plus a zero-initialized low-rank update."""
    def __init__(self, base, rank, alpha):
        super().__init__()
        require(isinstance(base, nn.Linear) and rank > 0 and math.isfinite(alpha) and alpha > 0,
                'LoRA requires a linear layer and positive rank/alpha')
        self.base = base.requires_grad_(False)
        self.scale = alpha / rank
        self.lora_A = nn.Parameter(base.weight.new_empty(rank, base.in_features))
        self.lora_B = nn.Parameter(base.weight.new_zeros(base.out_features, rank))
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))

    def forward(self, x):
        return self.base(x) + F.linear(F.linear(x, self.lora_A), self.lora_B) * self.scale

    def merged(self):
        with torch.no_grad():
            self.base.weight.add_((self.lora_B @ self.lora_A) * self.scale)
        return self.base


def install_lora(encoder, rank, alpha, tail_blocks):
    encoder.backbone.requires_grad_(False)
    layers = encoder.backbone.encoder.layers
    require(1 <= tail_blocks <= len(layers), 'Invalid LoRA tail block count')
    paths = []
    for index in range(len(layers) - tail_blocks, len(layers)):
        attention = layers[index].self_attn
        for name in ('q_proj', 'v_proj'):
            setattr(attention, name, LoRALinear(getattr(attention, name), rank, alpha))
            paths.append(f'encoder.layers.{index}.self_attn.{name}')
    return paths


def merge_lora(encoder):
    merged = []
    for name, module in list(encoder.backbone.named_modules()):
        if isinstance(module, LoRALinear):
            parent_name, leaf = name.rsplit('.', 1)
            parent = encoder.backbone.get_submodule(parent_name)
            setattr(parent, leaf, module.merged())
            merged.append(name)
    require(merged, 'No LoRA projections to merge')
    require(not any(isinstance(m, LoRALinear) for m in encoder.modules()), 'Unmerged LoRA module remains')
    return merged


def load_candidate(args, candidate, baseline_head):
    from safetensors.torch import load_file
    encoder, head = ft.build_models(args.model_dir, args.device, baseline_head)
    ft.load_state(encoder, head, load_file(str(contained(args.run_dir, candidate['checkpoint']))))
    if candidate['family'] == 'instrument_attention':
        specialist = InstrumentEncoder(encoder, head)
        extra = contained(args.run_dir, candidate['extra_checkpoint'])
        specialist.load_extra(load_file(str(extra)))
        encoder, head = specialist, InstrumentOutput(head)
    encoder.eval().requires_grad_(False)
    head.eval().requires_grad_(False)
    return encoder, head
