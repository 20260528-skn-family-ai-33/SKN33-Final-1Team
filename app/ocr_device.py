"""Choose an executable OCR device, rather than guessing from a GPU's presence."""
from dataclasses import dataclass
from contextlib import redirect_stdout
from functools import lru_cache
import os
import platform
import sys


@dataclass(frozen=True)
class DeviceSelection:
    requested: str
    device: str
    reason: str = ''


@lru_cache(maxsize=1)
def accelerator_availability():
    try:
        with redirect_stdout(sys.stderr):
            import torch
            return {'cuda': torch.cuda.is_available(),
                    'mps': torch.backends.mps.is_available()}
    except (ImportError, OSError, RuntimeError):
        return {'cuda': False, 'mps': False}


def select_device():
    requested = os.environ.get('LATTE_OCR_DEVICE', 'auto').strip().lower()
    if requested not in ('auto', 'cpu', 'cuda', 'mps'):
        raise ValueError('LATTE_OCR_DEVICE는 auto, cpu, cuda, mps 중 하나여야 합니다.')
    if requested == 'cpu':
        return DeviceSelection(requested, 'cpu')
    system, machine = platform.system(), platform.machine().lower()
    if system == 'Darwin' and machine not in ('arm64', 'aarch64'):
        return DeviceSelection(requested, 'cpu', 'Intel Mac은 CPU로 실행합니다.')
    target = 'mps' if system == 'Darwin' else 'cuda'
    if requested != 'auto' and requested != target:
        return DeviceSelection(requested, 'cpu', f'{system}에서 {requested}를 사용할 수 없어 CPU로 실행합니다.')
    if accelerator_availability()[target]:
        return DeviceSelection(requested, target)
    return DeviceSelection(requested, 'cpu', f'{target.upper()} 런타임을 사용할 수 없어 CPU로 실행합니다.')
