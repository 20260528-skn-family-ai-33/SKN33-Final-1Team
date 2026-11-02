"""Durable MCP receipts prevent replay from silently spending twice."""
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import Created, Identity, json_type


class ToolCall(Identity, Created, Base):
    __tablename__ = "cc_tool_calls"
    __table_args__ = (UniqueConstraint("job_id", "operation", "ordinal"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("cc_jobs.id"), index=True)
    operation: Mapped[str] = mapped_column(String(40))
    ordinal: Mapped[str] = mapped_column(String(40), default="0")
    state: Mapped[str] = mapped_column(String(20), default="running")
    result: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    error: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
