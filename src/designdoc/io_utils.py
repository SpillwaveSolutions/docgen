"""Filesystem helpers with crash-safe semantics.

atomic_write writes to a sibling .tmp file then os.replace(): on POSIX this
is atomic. A SIGKILL between the two steps leaves either a partial .tmp
(ignored on next run) or a complete target — never a truncated target.
"""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)
