"""In-memory build context that is written to disk in one step."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path, PurePosixPath

MARKER = ".linuxlive-context"


class CompileError(RuntimeError):
    """The spec could not be compiled, or the output directory is unsafe to use."""


class BuildContext:
    """Collects every file of a build context with its mode, then writes them."""

    def __init__(self) -> None:
        self._files: dict[str, tuple[bytes, int]] = {}

    def add_text(self, path: str, text: str, *, mode: int = 0o644) -> None:
        text = text.replace("\r\n", "\n")
        if not text.endswith("\n"):
            text += "\n"
        self._add(path, text.encode("utf-8"), mode)

    def add_bytes(self, path: str, data: bytes, *, mode: int = 0o644) -> None:
        self._add(path, data, mode)

    def _add(self, path: str, data: bytes, mode: int) -> None:
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise CompileError(f"invalid build context path {path!r}")
        key = pure.as_posix()
        if key in self._files:
            raise CompileError(f"{key} would be written twice")
        self._files[key] = (data, mode)

    def paths(self) -> list[str]:
        return sorted(self._files)

    def files(self) -> dict[str, tuple[bytes, int]]:
        """Copy of path -> (content, mode) for every file."""
        return dict(self._files)

    def manifest(self) -> str:
        """One line per file: octal mode, SHA-256, path. Pins modes even on Windows."""
        lines = [
            f"{mode:04o} {hashlib.sha256(data).hexdigest()} {path}"
            for path, (data, mode) in sorted(self._files.items())
        ]
        return "\n".join(lines) + "\n"

    def write(self, outdir: Path) -> None:
        outdir = Path(outdir)
        if outdir.exists():
            if not outdir.is_dir():
                raise CompileError(f"{outdir} exists and is not a directory")
            if any(outdir.iterdir()):
                if not (outdir / MARKER).is_file():
                    raise CompileError(
                        f"{outdir} is not empty and was not written by linuxlive; refusing to overwrite it"
                    )
                shutil.rmtree(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        for path, (data, mode) in sorted(self._files.items()):
            target = outdir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            os.chmod(target, mode)
        (outdir / "MANIFEST").write_bytes(self.manifest().encode("utf-8"))
        (outdir / MARKER).write_bytes(b"written by linuxlive compile\n")
