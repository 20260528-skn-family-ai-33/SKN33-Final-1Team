"""RunPod-only bounded Qwen forward/backward smoke test; no optimizer or saves."""
import math

import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image, ImageOps
from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration


MODEL = '/workspace/models/qwen3.5-4b'
IMAGE = '/workspace/highres-poster-20261028/packet/images/NEW112.jpg'


class LoRALinear(nn.Module):
    def __init__(self, original, rank=4):
        super().__init__()
        self.original = original
        self.a = nn.Linear(original.in_features, rank, bias=False,
                           device=original.weight.device, dtype=original.weight.dtype)
        self.b = nn.Linear(rank, original.out_features, bias=False,
                           device=original.weight.device, dtype=original.weight.dtype)
        nn.init.kaiming_uniform_(self.a.weight, a=math.sqrt(5))
        nn.init.zeros_(self.b.weight)

    def forward(self, x):
        return self.original(x) + self.b(self.a(x))


def main():
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is unavailable')
    free, total = torch.cuda.mem_get_info(0)
    print(f'GPU free: {free / 1024**3:.2f} GiB; total: {total / 1024**3:.2f} GiB', flush=True)
    if free < 14 * 1024**3:
        raise RuntimeError('Less than 14 GiB GPU memory free; stopping without loading weights')
    # Leave room for other processes. This only limits this process.
    torch.cuda.set_per_process_memory_fraction(0.48, 0)
    torch.cuda.reset_peak_memory_stats(0)
    processor = AutoProcessor.from_pretrained(MODEL, local_files_only=True, trust_remote_code=False)
    model = Qwen3_5ForConditionalGeneration.from_pretrained(
        MODEL, local_files_only=True, trust_remote_code=False, use_safetensors=True,
        dtype=torch.bfloat16, attn_implementation='sdpa').to('cuda:0')
    model.eval().requires_grad_(False)
    for block in model.model.visual.blocks[-2:]:
        block.attn.qkv = LoRALinear(block.attn.qkv)
    trainable = [p for p in model.parameters() if p.requires_grad]
    print('LoRA trainable parameters:', sum(p.numel() for p in trainable), flush=True)
    if not trainable:
        raise RuntimeError('No trainable parameters')
    with Image.open(IMAGE) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
        original = image.size
        image.thumbnail((768, 768), Image.Resampling.LANCZOS)
        messages = [{'role': 'user', 'content': [
            {'type': 'image'}, {'type': 'text', 'text': 'Is there a person? Answer yes or no.'}]}]
        text = processor.apply_chat_template(messages, tokenize=False,
                                             add_generation_prompt=True, enable_thinking=False)
        inputs = processor(text=[text], images=[image], return_tensors='pt')
    print('Image:', original, '->', image.size, '; grid:', inputs['image_grid_thw'].tolist(), flush=True)
    inputs = {k: v.to('cuda:0', dtype=torch.bfloat16) if v.is_floating_point() else v.to('cuda:0')
              for k, v in inputs.items()}
    # A synthetic one-token target checks gradients only; it is not a training label.
    ids = processor.tokenizer.encode('yes', add_special_tokens=False)
    if not ids:
        raise RuntimeError('Could not encode smoke-test token')
    output = model(**inputs, use_cache=False, logits_to_keep=1)
    loss = F.cross_entropy(output.logits[:, -1, :].float(),
                           torch.tensor([ids[0]], device='cuda:0'))
    if not torch.isfinite(loss):
        raise RuntimeError('Non-finite loss')
    loss.backward()
    valid = [p.grad for p in trainable if p.grad is not None]
    if not valid or not all(torch.isfinite(g).all().item() for g in valid):
        raise RuntimeError('No finite LoRA gradients')
    if not any(torch.count_nonzero(g).item() for g in valid):
        raise RuntimeError('All LoRA gradients are zero')
    torch.cuda.synchronize()
    print(f'SMOKE OK: loss={loss.item():.4f}, peak GPU={torch.cuda.max_memory_allocated()/1024**3:.2f} GiB', flush=True)
    print('No optimizer step, model save, or base-weight update was performed.', flush=True)


if __name__ == '__main__':
    main()
