import logging
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from university_pz_parser.extractors import formats

log = logging.getLogger(__name__)

PLAIN = {
    ".txt",
    ".md",
    ".csv",
    ".py",
    ".sql",
    ".json",
    ".html",
    ".xml",
    ".yaml",
    ".yml",
}
HANDLERS: dict[str, Callable[[Path], str]] = {
    ".docx": formats.docx_text,
    ".pptx": formats.pptx_text,
    ".xlsx": formats.xlsx_text,
    ".pdf": formats.pdf_text,
    ".ipynb": formats.ipynb_text,
    **{suffix: formats.plain_text for suffix in PLAIN},
}
MAX_ZIP_DEPTH = 2


@dataclass(frozen=True)
class Extraction:
    text: str | None
    status: str


class ExtractorRegistry:
    def __init__(self, limit: int = 200_000):
        self.limit = limit

    def extract(self, path: Path, name: str | None, mime: str | None) -> Extraction:
        return self._cap(self._extract(path, name, 0))

    def _cap(self, result: Extraction) -> Extraction:
        if result.text is not None and len(result.text) > self.limit:
            return Extraction(result.text[: self.limit], "truncated")
        return result

    def _extract(self, path: Path, name: str | None, depth: int) -> Extraction:
        suffix = Path(name or path.name).suffix.lower()
        try:
            if suffix == ".zip":
                return self._zip(path, depth)
            handler = HANDLERS.get(suffix)
            if handler is None:
                return Extraction(None, "unsupported")
            text = handler(path)
        except Exception:
            log.warning("extraction failed for %s", name or path, exc_info=True)
            return Extraction(None, "failed")
        if suffix == ".pdf" and not text.strip():
            return Extraction(None, "needs_ocr")
        return Extraction(text, "ok")

    def _zip(self, path: Path, depth: int) -> Extraction:
        if depth >= MAX_ZIP_DEPTH:
            return Extraction(None, "unsupported")
        parts = []
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as tmp:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                target = Path(tmp) / f"m{len(parts)}{Path(info.filename).suffix}"
                target.write_bytes(archive.read(info))
                inner = self._extract(target, info.filename, depth + 1)
                body = inner.text if inner.text else f"[{inner.status}]"
                shown = info.filename
                if not info.flag_bits & 0x800:
                    try:
                        shown = info.filename.encode("cp437").decode("cp866")
                    except UnicodeError:
                        pass
                parts.append(f"### {shown}\n{body}")
        return Extraction("\n\n".join(parts), "ok")
