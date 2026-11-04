"""Immutable UTF-8 requirement inputs; no normalization or model inference."""
from .ports import AdapterFailure

MAX_TEXT_CHARACTERS=60000


def inspect_text(raw):
    try:
        if not isinstance(raw,bytes):raise ValueError()
        text=raw.decode('utf-8')
        if not text.strip() or text.startswith('\ufeff'):raise ValueError()
        if len(text)>MAX_TEXT_CHARACTERS:
            raise AdapterFailure('REQUIREMENT_TEXT_TOO_LARGE','Requirement text exceeds the character limit.')
        if any((ord(c)<32 and c not in '\r\n\t') or ord(c)==127 for c in text):raise ValueError()
    except (ValueError,UnicodeError):
        raise AdapterFailure('REQUIREMENT_TEXT_INVALID','A nonblank UTF-8 requirement source is required.') from None
    return 1


def inspect_requirement(raw,media_type,limits=None):
    if media_type=='text/plain':return inspect_text(raw)
    if media_type!='application/pdf':raise AdapterFailure('FILE_MEDIA_TYPE_MISMATCH','Unsupported requirement source.')
    from .files import inspect_document
    return inspect_document(raw,limits)
