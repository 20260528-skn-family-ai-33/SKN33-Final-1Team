"""Worker-produced, immutable admission description; web never imports Paddle."""
import os
from pathlib import Path
from ..config import ConfigurationError
from ..security.errors import AuthError
from ..hashing.canonical import canonical,loads
from .pipeline import code_revision


class QueuedPipeline:
    def __init__(self,path):
        try:
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as stream:
                import stat
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError()
                raw=stream.read(65537)
            if len(raw)>65536:raise ValueError()
            value=loads(raw)
            if set(value)!={'contract_version','requested_config'} or value['contract_version']!='cc-worker-config-v1':raise ValueError()
            config=value['requested_config']
            if (config['pipeline']!='paddle_full_v1' or config['execution_target']!='local' or config['device_policy']!=['cpu']
                or config['preprocessing']!={'version':'cc-display-2x-lanczos-v1','ocr_scale':2}
                or config['code_revision']!=code_revision() or not isinstance(config['model_revision'],dict)):
                raise ValueError()
            self.raw=canonical(config)
        except (OSError,ValueError,TypeError,KeyError,AuthError):
            raise ConfigurationError('A matching worker-produced REVIEW_CONFIG_FILE is required.') from None

    def describe(self):return loads(self.raw)


def write_config(path,config):
    """Explicit deployment artifact, no overwrite and no secret or machine path."""
    raw=canonical(dict(contract_version='cc-worker-config-v1',requested_config=config))
    if len(raw)>65536:raise ConfigurationError('Worker description exceeds its limit.')
    fd=os.open(Path(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
