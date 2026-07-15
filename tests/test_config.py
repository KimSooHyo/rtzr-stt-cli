from __future__ import annotations

import pytest

from rtzr_stt.config import CredentialError, load_credentials


def test_credentials_load_from_file_and_environment_takes_precedence(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "STT_CLIENT_ID=file-id\nSTT_CLIENT_SECRET=file-secret\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("STT_CLIENT_ID", raising=False)
    monkeypatch.delenv("STT_CLIENT_SECRET", raising=False)

    assert load_credentials(env_file) == ("file-id", "file-secret")

    monkeypatch.setenv("STT_CLIENT_ID", "  environment-id  ")
    monkeypatch.setenv("STT_CLIENT_SECRET", "  environment-secret  ")
    assert load_credentials(env_file) == ("environment-id", "environment-secret")


def test_whitespace_only_credentials_are_rejected(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "STT_CLIENT_ID=   \nSTT_CLIENT_SECRET=   \n",
        encoding="utf-8",
    )
    monkeypatch.delenv("STT_CLIENT_ID", raising=False)
    monkeypatch.delenv("STT_CLIENT_SECRET", raising=False)

    with pytest.raises(CredentialError):
        load_credentials(env_file)
