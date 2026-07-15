from __future__ import annotations

import csv
import json
import wave
from pathlib import Path

import pytest

from rtzr_stt import cli
from rtzr_stt.evaluation import (
    ManifestError,
    evaluate_manifest,
    load_manifest,
)


def write_wav(path: Path, duration_seconds: float = 0.1) -> None:
    frame_rate = 8000
    frames = int(frame_rate * duration_seconds)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(frame_rate)
        audio.writeframes(b"\0\0" * frames)


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=[
                "sample_id",
                "audio_path",
                "reference_path",
                "session_id",
                "stratum",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def manifest_row(sample_id: str = "sample-1") -> dict[str, str]:
    return {
        "sample_id": sample_id,
        "audio_path": "sample.wav",
        "reference_path": "sample.txt",
        "session_id": "session-1",
        "stratum": "clean",
    }


def completed_response(message: str = "안녕하세요") -> dict:
    return {
        "id": "job-1",
        "status": "completed",
        "results": {
            "utterances": [
                {
                    "start_at": 0,
                    "duration": 100,
                    "msg": message,
                    "spk": 0,
                    "lang": "ko",
                }
            ]
        },
    }


def test_manifest_validates_duplicates_missing_and_empty_reference(tmp_path):
    audio = tmp_path / "sample.wav"
    reference = tmp_path / "sample.txt"
    manifest = tmp_path / "manifest.csv"
    write_wav(audio)
    reference.write_text("정답", encoding="utf-8")

    write_manifest(manifest, [manifest_row(), manifest_row()])
    with pytest.raises(ManifestError, match="중복"):
        load_manifest(manifest)

    write_manifest(manifest, [{**manifest_row(), "audio_path": "missing.wav"}])
    with pytest.raises(ManifestError, match="파일이 없습니다"):
        load_manifest(manifest)

    reference.write_text("n/ !!!", encoding="utf-8")
    write_manifest(manifest, [manifest_row()])
    with pytest.raises(ManifestError, match="정규화 후"):
        load_manifest(manifest)


def test_over_limit_fails_before_client_creation(tmp_path, monkeypatch, capsys):
    audio = tmp_path / "sample.wav"
    reference = tmp_path / "sample.txt"
    manifest = tmp_path / "manifest.csv"
    audio.write_bytes(b"not-read-because-duration-is-mocked")
    reference.write_text("정답", encoding="utf-8")
    write_manifest(manifest, [manifest_row()])
    monkeypatch.setattr("rtzr_stt.evaluation.wav_duration_seconds", lambda _: 901.0)

    def unexpected_client(_):
        raise AssertionError("invalid manifest must not create a network client")

    monkeypatch.setattr(cli, "_build_client", unexpected_client)
    exit_code = cli.main(
        [
            "evaluate",
            str(manifest),
            "--output-dir",
            str(tmp_path / "result"),
            "--max-audio-minutes",
            "15",
        ]
    )
    assert exit_code == 2
    assert "API는 호출되지 않았습니다" in capsys.readouterr().err


def test_resume_reuses_matching_audio_and_config_cache(tmp_path):
    audio = tmp_path / "sample.wav"
    reference = tmp_path / "sample.txt"
    manifest_path = tmp_path / "manifest.csv"
    output_dir = tmp_path / "results"
    write_wav(audio)
    reference.write_text("안녕하세요", encoding="utf-8")
    write_manifest(manifest_path, [manifest_row()])
    manifest = load_manifest(manifest_path)

    class FirstClient:
        calls = 0

        def transcribe(self, *args, **kwargs):
            self.calls += 1
            return completed_response()

    first = FirstClient()
    first_summary = evaluate_manifest(first, manifest, output_dir)
    assert first.calls == 1
    assert first_summary["corpus"]["cer"] == 0

    class NoNetworkClient:
        def transcribe(self, *args, **kwargs):
            raise AssertionError("resume cache hit must not call the API")

    resumed = evaluate_manifest(NoNetworkClient(), manifest, output_dir, resume=True)
    assert resumed["corpus"]["cer"] == 0
    assert resumed["samples"][0]["cache_hit"] is True


def test_resume_rejects_changed_audio_hash(tmp_path):
    audio = tmp_path / "sample.wav"
    reference = tmp_path / "sample.txt"
    manifest_path = tmp_path / "manifest.csv"
    output_dir = tmp_path / "results"
    write_wav(audio)
    reference.write_text("안녕하세요", encoding="utf-8")
    write_manifest(manifest_path, [manifest_row()])
    manifest = load_manifest(manifest_path)

    class CountingClient:
        def __init__(self):
            self.calls = 0

        def transcribe(self, *args, **kwargs):
            self.calls += 1
            return completed_response()

    first = CountingClient()
    evaluate_manifest(first, manifest, output_dir)
    assert first.calls == 1

    with audio.open("ab") as destination:
        destination.write(b"changed")
    changed = CountingClient()
    evaluate_manifest(changed, manifest, output_dir, resume=True)
    assert changed.calls == 1


def test_transcribe_with_reference_writes_metrics(tmp_path, monkeypatch, capsys):
    audio = tmp_path / "sample.wav"
    reference = tmp_path / "sample.txt"
    output = tmp_path / "output"
    audio.write_bytes(b"audio")
    reference.write_text("안녕하세요", encoding="utf-8")

    class FakeClient:
        def transcribe(self, *args, **kwargs):
            return completed_response()

    monkeypatch.setattr(cli, "_build_client", lambda _: FakeClient())
    exit_code = cli.main(
        [
            "transcribe",
            str(audio),
            "--format",
            "all",
            "--output-dir",
            str(output),
            "--reference",
            str(reference),
        ]
    )
    assert exit_code == 0
    assert (output / "response.json").is_file()
    assert (output / "transcript.txt").is_file()
    assert (output / "transcript.srt").is_file()
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["cer"] == 0
    assert "CER: 0.00% (낮을수록 좋음)" in capsys.readouterr().out
