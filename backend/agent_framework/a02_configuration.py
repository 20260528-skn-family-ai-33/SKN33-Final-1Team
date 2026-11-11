"""Trusted worker settings only; no imports that open files, DBs or HTTP clients."""
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field,model_validator
from .contracts import Contract


class PDFExtractionConfiguration(Contract):
    mode: Literal['mock','qwen']
    structuring: Literal['semantic','literal_legacy'] = 'semantic'
    presentation: bool = Field(default=False,strict=True)
    endpoint: str | None = None
    model: str = Field(default='Qwen/Qwen3.5-4B',min_length=1,max_length=200)
    parser_version: str = Field(default='1.28.2',pattern=r'^\d+\.\d+\.\d+$')
    max_text_chars: int = Field(default=32000,ge=100,le=60000,strict=True)
    timeout_seconds: int = Field(default=900,ge=1,le=900,strict=True)
    max_output_tokens: int = Field(default=8192,ge=128,le=16384,strict=True)

    @model_validator(mode='after')
    def endpoint_matches_mode(self):
        if self.presentation and (self.mode!='qwen' or self.structuring!='semantic'):
            raise ValueError('Presentation requires Qwen semantic extraction')
        if self.mode=='mock':
            if self.endpoint is not None:raise ValueError('mock mode must not have a model endpoint')
        else:
            value=self.endpoint
            if not value or any(ord(c)<33 for c in value):raise ValueError('Qwen needs an explicit endpoint')
            parsed=urlsplit(value)
            if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or not parsed.path.endswith('/chat/completions')):
                raise ValueError('Use a trusted HTTP chat completions endpoint without credentials')
            parsed.port
        return self
