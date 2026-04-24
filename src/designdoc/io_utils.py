"""Filesystem helpers with crash-safe semantics.

atomic_write writes to a sibling .tmp file then os.replace(): on POSIX this
is atomic. A SIGKILL between the two steps leaves either a partial .tmp
(ignored on next run) or a complete target — never a truncated target.

sha1_keyed is a shared digest helper used by stages that checkpoint on input
hashes (s4 package rollups, s6 tech-debt, s7 system rollup). It must produce
byte-identical output to the prior per-stage `_hash_*` helpers so existing
`.designdoc-state.json` artifact_index entries remain valid across upgrades.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)


def sha1_keyed(items: dict[str, str]) -> str:
    """SHA1 hex digest of a dict, keyed by sorted keys.

    For each key in ``sorted(items)`` the digest absorbs::

        key_utf8 + b"\\0" + value_utf8 + b"\\n"

    This encoding is the shared shape of the pre-refactor ``_hash_class_docs``
    (s4), ``_hash_readmes`` (s7), and ``_hash_deps`` / ``_hash_dep`` (s6)
    helpers. For s6, the triple ``(name, pinned, source)`` is flattened into a
    single dict entry whose value is ``f"{pinned}\\0{source}"`` so the absorbed
    bytes remain ``name\\0pinned\\0source\\n`` — byte-identical to the legacy
    implementation. Stability across Python runs and platforms is required;
    used for resume / incremental bookkeeping in state.json.
    """
    h = hashlib.sha1()
    for key in sorted(items):
        h.update(key.encode("utf-8"))
        h.update(b"\0")
        h.update(items[key].encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()
