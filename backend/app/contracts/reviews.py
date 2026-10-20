"""Only persistent resource IDs are accepted; execution settings are server owned."""
from uuid import UUID
from pydantic import field_validator
from .business import Strict


class ReviewCreate(Strict):
    project_id: str
    image_id: str
    criteria_version_id: str

    @field_validator('project_id','image_id','criteria_version_id')
    @classmethod
    def identifier(cls,value): return str(UUID(value))


class Retry(Strict): pass
