import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    size: int
    path: Path


class FileStore:
    def __init__(self, root: Path):
        self.root = root
        self.blobs = root / "blobs"
        self.tmp = root / "tmp"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)

    def new_tmp_dir(self) -> Path:
        return Path(tempfile.mkdtemp(dir=self.tmp))

    def put(self, src: Path, name: str | None) -> StoredFile:
        digest = hashlib.sha256()
        size = 0
        with src.open("rb") as fh:
            while chunk := fh.read(1 << 20):
                digest.update(chunk)
                size += len(chunk)
        sha = digest.hexdigest()
        suffix = Path(name or src.name).suffix.lower()
        dest = self.blobs / sha[:2] / f"{sha}{suffix}"
        if dest.exists():
            src.unlink()
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dest)
        return StoredFile(sha, size, dest)

    def resolve(self, local_path: str) -> Path:
        return Path(local_path)
