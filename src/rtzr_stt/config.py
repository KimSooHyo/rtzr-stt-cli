from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

BASE_URL = "https://openapi.vito.ai"

DEFAULT_TRANSCRIBE_CONFIG: dict[str, Any] = {
    "model_name": "sommers",
    "language": "ko",
    "domain": "GENERAL",
    "use_diarization": False,
    "use_itn": True,
    "use_disfluency_filter": False,
    "use_profanity_filter": False,
    "use_paragraph_splitter": False,
    "use_word_timestamp": False,
    "keywords": [],
}


class CredentialError(ValueError):
    """Raised when API credentials are unavailable."""


def canonical_config_json(config: dict[str, Any] | None = None) -> str:
    """Return a stable JSON representation used by requests and cache keys."""
    return json.dumps(
        config or DEFAULT_TRANSCRIBE_CONFIG,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def config_sha256(config: dict[str, Any] | None = None) -> str:
    return hashlib.sha256(canonical_config_json(config).encode("utf-8")).hexdigest()


def load_credentials(env_file: str | Path = ".env") -> tuple[str, str]:
    """Load generic public-facing credential names without logging values."""
    path = Path(env_file)
    file_values = dotenv_values(path) if path.is_file() else {}
    client_id = os.environ.get("STT_CLIENT_ID") or file_values.get("STT_CLIENT_ID")
    client_secret = os.environ.get("STT_CLIENT_SECRET") or file_values.get("STT_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise CredentialError(
            "STT_CLIENT_ID와 STT_CLIENT_SECRET을 환경 변수 또는 지정한 .env에 설정하세요."
        )
    return str(client_id), str(client_secret)
