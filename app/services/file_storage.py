import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import settings


class UploadTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class StoredFile:
    size_bytes: int
    content_hash: str


class FileStorage(Protocol):
    def save(self, source: BinaryIO, storage_key: str) -> StoredFile: ...

    def delete(self, storage_key: str) -> None: ...


class LocalFileStorage:
    chunk_size = 1024 * 1024

    def __init__(self, root: str | Path, max_bytes: int):
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes

    def _target(self, storage_key: str) -> Path:
        target = (self.root / storage_key).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError("invalid storage key")
        return target

    def save(self, source: BinaryIO, storage_key: str) -> StoredFile:
        target = self._target(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, delete=False
            ) as temporary:
                temp_path = Path(temporary.name)
                while chunk := source.read(self.chunk_size):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise UploadTooLargeError
                    digest.update(chunk)
                    temporary.write(chunk)
            os.replace(temp_path, target)
            return StoredFile(size_bytes=size, content_hash=digest.hexdigest())
        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise

    def delete(self, storage_key: str) -> None:
        self._target(storage_key).unlink(missing_ok=True)


def get_file_storage() -> FileStorage:
    return LocalFileStorage(settings.UPLOAD_DIR, settings.MAX_UPLOAD_BYTES)
