from __future__ import annotations

import json
import os

import pytest

from rtzr_stt.io import write_json_atomic, write_text_atomic


def temporary_files_for(destination):
    return list(destination.parent.glob(f".{destination.name}.*.tmp"))


def test_write_text_atomic_replaces_with_utf8_and_leaves_no_temporary_file(tmp_path):
    destination = tmp_path / "nested" / "transcript.txt"
    destination.parent.mkdir()
    destination.write_text("old content", encoding="utf-8")

    write_text_atomic(destination, "안녕하세요\n")

    assert destination.read_text(encoding="utf-8") == "안녕하세요\n"
    assert temporary_files_for(destination) == []


def test_json_is_standard_and_rejects_nan(tmp_path):
    destination = tmp_path / "response.json"

    write_json_atomic(destination, {"message": "안녕하세요", "status": "completed"})

    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "message": "안녕하세요",
        "status": "completed",
    }

    with pytest.raises(ValueError):
        write_json_atomic(destination, {"value": float("nan")})
    assert json.loads(destination.read_text(encoding="utf-8"))["status"] == "completed"


def test_write_text_atomic_cleans_up_if_replace_fails(tmp_path, monkeypatch):
    destination = tmp_path / "result.txt"
    destination.write_text("old content", encoding="utf-8")

    def fail_replace(source, target):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_text_atomic(destination, "new content")

    assert destination.read_text(encoding="utf-8") == "old content"
    assert temporary_files_for(destination) == []
