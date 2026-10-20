"""Explicit JSON security logging with an allowlist; never format input or exceptions."""
import json,logging
from threading import Lock

_lock=Lock()


class SecurityFormatter(logging.Formatter):
    def format(self,record):
        fields={name:getattr(record,name) for name in ('event','request_id','user_id','reason','operator') if hasattr(record,name)}
        return json.dumps({'level':record.levelname,**fields},ensure_ascii=False)


def configure_security_logging():
    logger=logging.getLogger('creative_checker.security')
    with _lock:
        if not any(getattr(handler,'_cc_security',False) for handler in logger.handlers):
            handler=logging.StreamHandler();handler._cc_security=True;handler.setFormatter(SecurityFormatter());logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
