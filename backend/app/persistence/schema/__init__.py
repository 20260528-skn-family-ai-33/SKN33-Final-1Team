"""Core metadata only; importing it never connects or creates tables."""
from sqlalchemy import MetaData

metadata = MetaData(schema="public")

# Register all C02-C06 tables before binding cross-table relationships.
from . import identity  # noqa: E402, F401
from . import files  # noqa: E402, F401
from . import ocr  # noqa: E402, F401
from . import criteria  # noqa: E402, F401
from . import reviews  # noqa: E402, F401
from . import relations  # noqa: E402, F401

# M007 indexes are part of the current metadata; guards remain explicit frozen SQL.
from .indexes import register_indexes  # noqa: E402
register_indexes(metadata)
