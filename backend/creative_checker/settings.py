"""Runtime configuration. Importing the package never connects to a service."""

from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default="postgresql+psycopg://postgres@localhost:5432/creative_checker", repr=False)
    data_dir: Path = Path("data/creative_checker")
    qwen_base_url: str = ""
    qwen_api_key: str = field(default="", repr=False)
    qwen_model: str = "Qwen/Qwen3.5-4B"
    qwen_revision: str = "unverified"
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = field(default="", repr=False)
    storage_backend: str = "local"
    s3_bucket: str = ""
    s3_region: str = "ap-northeast-2"
    mcp_url: str = ""
    mcp_token: str = field(default="", repr=False)
    session_cookie: str = "cc_session"
    cookie_secure: bool = True
    session_hours: int = 12
    testing: bool = False

    def inference_profile(self) -> dict:
        # Normalize line endings so API and Linux worker builds identify the same pipeline.
        digest = hashlib.sha256()
        for name in ("prompts.py", "qwen.py", "documents.py", "media.py", "inspection.py", "tools.py", "orchestration.py"):
            digest.update(name.encode())
            digest.update(Path(__file__).with_name(name).read_text(encoding="utf-8").encode())
        return {"model": self.qwen_model, "declared_revision": self.qwen_revision,
                "pipeline_sha256": digest.hexdigest(), "temperature": 0,
                "max_output_tokens": 4096, "max_calls_per_stage": 32, "stage_seconds": 600}

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir))
        if self.database_url.startswith("sqlite"):
            if not self.testing:
                raise ValueError("SQLite is available only with CC_TESTING=1")
        elif not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("DATABASE_URL must use PostgreSQL")
        if self.storage_backend not in {"local", "s3"}:
            raise ValueError("CC_STORAGE_BACKEND must be local or s3")
        if self.storage_backend == "s3" and not self.s3_bucket:
            raise ValueError("CC_S3_BUCKET is required for S3 storage")
        if self.session_hours < 1 or self.session_hours > 168:
            raise ValueError("CC_SESSION_HOURS must be between 1 and 168")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", Settings.__dataclass_fields__["database_url"].default),
        data_dir=Path(os.getenv("CC_DATA_DIR", "data/creative_checker")),
        qwen_base_url=os.getenv("CC_QWEN_BASE_URL", ""),
        qwen_api_key=os.getenv("CC_QWEN_API_KEY", ""),
        qwen_model=os.getenv("CC_QWEN_MODEL", "Qwen/Qwen3.5-4B"),
        qwen_revision=os.getenv("CC_QWEN_REVISION", "unverified"),
        neo4j_uri=os.getenv("CC_NEO4J_URI", ""),
        neo4j_user=os.getenv("CC_NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("CC_NEO4J_PASSWORD", ""),
        storage_backend=os.getenv("CC_STORAGE_BACKEND", "local"),
        s3_bucket=os.getenv("CC_S3_BUCKET", ""),
        s3_region=os.getenv("CC_S3_REGION", "ap-northeast-2"),
        mcp_url=os.getenv("CC_MCP_URL", ""),
        mcp_token=os.getenv("CC_MCP_TOKEN", ""),
        session_cookie=os.getenv("CC_SESSION_COOKIE", "cc_session"),
        cookie_secure=os.getenv("CC_COOKIE_SECURE", "true").lower() not in {"0", "false", "no"},
        session_hours=int(os.getenv("CC_SESSION_HOURS", "12")),
        testing=os.getenv("CC_TESTING", "").lower() in {"1", "true", "yes"},
    )
