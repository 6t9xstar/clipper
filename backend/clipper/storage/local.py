"""Local filesystem storage.

Keys are server-generated, slash-separated, and validated; user-supplied names never become paths.
Writes are atomic (temp file + os.replace): a crash never leaves a half-written file under its final key.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}


class StorageError(Exception):
    pass


class UnsafePathError(StorageError):
    pass


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "tmp").mkdir(exist_ok=True)

    def path(self, key: str) -> Path:
        """Resolve a storage key to an absolute path inside root, rejecting anything unsafe."""
        if not key or key.startswith("/") or "\\" in key:
            raise UnsafePathError("invalid storage key")
        parts = key.split("/")
        for p in parts:
            if not _SEGMENT.fullmatch(p) or p in {".", ".."} or p.split(".")[0].lower() in _RESERVED:
                raise UnsafePathError("invalid storage key segment")
        full = (self.root.joinpath(*parts)).resolve()
        if not full.is_relative_to(self.root):  # defence in depth against symlinks
            raise UnsafePathError("storage key escapes root")
        return full

    def exists(self, key: str) -> bool:
        return self.path(key).exists()

    @contextmanager
    def open_write(self, key: str) -> Iterator[BinaryIO]:
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".part-")
        try:
            with os.fdopen(fd, "wb") as fh:
                yield fh
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def write_bytes(self, key: str, data: bytes) -> None:
        with self.open_write(key) as fh:
            fh.write(data)

    def read_bytes(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def delete_prefix(self, prefix: str) -> None:
        """Hard-delete a key and everything under it (used for project deletion)."""
        p = self.path(prefix)
        if p == self.root:
            raise UnsafePathError("refusing to delete storage root")
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free
