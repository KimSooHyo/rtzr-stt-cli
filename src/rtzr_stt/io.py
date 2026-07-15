from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_text_atomic(path: str | Path, content: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    descriptor_is_open = True

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            descriptor_is_open = False
            output.write(content)
        os.replace(temporary, destination)
    except BaseException:
        if descriptor_is_open:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            temporary.unlink()
        raise


def write_json_atomic(path: str | Path, payload: dict[str, Any]) -> None:
    content = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    write_text_atomic(path, content + "\n")
