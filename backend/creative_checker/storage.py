"""Immutable file bytes; callers own authorization and FileRecord transactions."""

from pathlib import Path, PurePosixPath
import re

from .settings import Settings, get_settings


def validate_key(key: str) -> str:
    parts = PurePosixPath(key).parts
    if not parts or key.startswith("/") or "\\" in key or any(
        part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", part)
        for part in parts
    ) or "/".join(parts) != key:
        raise ValueError("Invalid storage key")
    return key


class LocalStorage:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / validate_key(key)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Storage path escapes the data directory")
        return path

    def put(self, key: str, data: bytes, media_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with path.open("xb") as stream:
                created = True
                stream.write(data)
                stream.flush()
        except FileExistsError:
            raise FileExistsError("Stored files cannot be overwritten") from None
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise
        return key

    def read(self, key: str, start: int | None = None, end: int | None = None) -> bytes:
        if start is not None and start < 0 or end is not None and (end < 0 or start is not None and end < start):
            raise ValueError("Invalid byte range")
        with self._path(key).open("rb") as stream:
            stream.seek(start or 0)
            return stream.read(-1 if end is None else end - (start or 0) + 1)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Storage:
    def __init__(self, bucket: str, region: str, client=None):
        if not bucket:
            raise ValueError("S3 bucket is required")
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client("s3", region_name=region, config=Config(
                connect_timeout=5, read_timeout=60,
                retries={"mode": "standard", "total_max_attempts": 3},
            ))
        self.client = client
        self.bucket = bucket

    def put(self, key: str, data: bytes, media_type: str = "application/octet-stream") -> str:
        # Bounded uploads use a conditional PUT: managed transfers allow overwrites.
        self.client.put_object(Bucket=self.bucket, Key=validate_key(key), Body=data,
                               ContentType=media_type, IfNoneMatch="*")
        return key

    def read(self, key: str, start: int | None = None, end: int | None = None) -> bytes:
        if start is not None and start < 0 or end is not None and (end < 0 or start is not None and end < start):
            raise ValueError("Invalid byte range")
        args = {"Bucket": self.bucket, "Key": validate_key(key)}
        if start is not None or end is not None:
            args["Range"] = f"bytes={start or 0}-{'' if end is None else end}"
        response = self.client.get_object(**args)
        try:
            return response["Body"].read()
        finally:
            response["Body"].close()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=validate_key(key))


def create_storage(settings: Settings | None = None) -> LocalStorage | S3Storage:
    settings = settings or get_settings()
    if settings.storage_backend == "local":
        return LocalStorage(settings.data_dir / "files")
    return S3Storage(settings.s3_bucket, settings.s3_region)
