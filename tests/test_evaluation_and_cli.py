from __future__ import annotations

import csv
import hashlib
import json
import wave
from pathlib import Path

import pytest

from rtzr_stt import cli
from rtzr_stt.config import DEFAULT_TRANSCRIBE_CONFIG
from rtzr_stt.evaluation import ManifestError, evaluate_manifest, load_manifest


def write_wav(path: Path, duration_seconds: float = 0.1) -> None:
    frame_rate = 8_000
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


def manifest_row(
    sample_id: str = "sample-1",
    *,
    audio_path: str = "sample.wav",
    reference_path: str = "sample.txt",
    session_id: str = "session-1",
    stratum: str = "clean",
) -> dict[str, str]:
    return {
        "sample_id": sample_id,
        "audio_path": audio_path,
        "reference_path": reference_path,
        "session_id": session_id,
        "stratum": stratum,
    }


def completed_response(message: str = "안녕하세요", job_id: str = "job-1") -> dict:
    return {
        "id": job_id,
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


def prepare_single_manifest(tmp_path: Path, reference_text: str = "안녕하세요") -> Path:
    write_wav(tmp_path / "sample.wav")
    (tmp_path / "sample.txt").write_text(reference_text, encoding="utf-8")
    manifest = tmp_path / "manifest.csv"
    write_manifest(manifest, [manifest_row()])
    return manifest


def expected_file_sha256(path: Path) -> str:
    """Calculate an expected digest independently from production I/O helpers."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_manifest_resolves_inputs_and_records_provenance(tmp_path):
    manifest_path = prepare_single_manifest(tmp_path)

    manifest = load_manifest(manifest_path)

    assert manifest.path == manifest_path.resolve()
    assert manifest.sha256 == expected_file_sha256(manifest_path)
    assert manifest.total_duration_seconds == pytest.approx(0.1)
    sample = manifest.samples[0]
    assert sample.audio_path == (tmp_path / "sample.wav").resolve()
    assert sample.reference_text == "안녕하세요"
    assert sample.audio_sha256 == expected_file_sha256(sample.audio_path)
    assert sample.reference_sha256 == expected_file_sha256(sample.reference_path)


def test_manifest_requires_all_columns(tmp_path):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("sample_id,audio_path\nsample-1,sample.wav\n", encoding="utf-8")

    with pytest.raises(ManifestError, match="필수 열"):
        load_manifest(manifest)


def test_manifest_rejects_empty_dataset(tmp_path):
    manifest = tmp_path / "manifest.csv"
    write_manifest(manifest, [])

    with pytest.raises(ManifestError, match="표본이 없습니다"):
        load_manifest(manifest)


def test_manifest_rejects_duplicate_ids_and_missing_inputs(tmp_path):
    manifest = prepare_single_manifest(tmp_path)
    write_manifest(manifest, [manifest_row(), manifest_row()])

    with pytest.raises(ManifestError, match="중복"):
        load_manifest(manifest)

    write_manifest(manifest, [manifest_row(audio_path="missing.wav")])
    with pytest.raises(ManifestError, match="파일이 없습니다"):
        load_manifest(manifest)


@pytest.mark.parametrize("sample_id", [".", "..", "../escape", "한글", "has space"])
def test_manifest_rejects_unsafe_sample_ids(tmp_path, sample_id):
    manifest = prepare_single_manifest(tmp_path)
    write_manifest(manifest, [manifest_row(sample_id)])

    with pytest.raises(ManifestError, match="sample_id"):
        load_manifest(manifest)


@pytest.mark.parametrize("field", ["session_id", "stratum"])
def test_manifest_requires_nonempty_metadata(tmp_path, field):
    manifest = prepare_single_manifest(tmp_path)
    row = manifest_row()
    row[field] = "  "
    write_manifest(manifest, [row])

    with pytest.raises(ManifestError, match=field):
        load_manifest(manifest)


def test_manifest_rejects_empty_or_non_utf8_reference(tmp_path):
    manifest = prepare_single_manifest(tmp_path)
    reference = tmp_path / "sample.txt"
    reference.write_text("n/ !!!", encoding="utf-8")

    with pytest.raises(ManifestError, match="정규화 후"):
        load_manifest(manifest)

    reference.write_bytes(b"\xff")
    with pytest.raises(ManifestError, match="UTF-8"):
        load_manifest(manifest)


def test_manifest_rejects_truncated_or_zero_length_wav(tmp_path):
    manifest = prepare_single_manifest(tmp_path)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"")

    with pytest.raises(ManifestError, match="WAV 파일을 읽을 수 없습니다"):
        load_manifest(manifest)

    write_wav(audio, duration_seconds=0)
    with pytest.raises(ManifestError, match="0초보다"):
        load_manifest(manifest)

    write_wav(audio, duration_seconds=1)
    audio.write_bytes(audio.read_bytes()[:44])
    with pytest.raises(ManifestError, match="프레임 수와 다릅니다"):
        load_manifest(manifest)


@pytest.mark.parametrize(
    "limit",
    [0, -1, float("nan"), float("inf"), float("-inf")],
)
def test_manifest_requires_finite_positive_audio_limit(tmp_path, limit):
    with pytest.raises(ManifestError, match="유한한 0보다 큰"):
        load_manifest(tmp_path / "manifest.csv", max_audio_minutes=limit)


def test_over_limit_fails_before_client_creation(tmp_path, monkeypatch, capsys):
    manifest = prepare_single_manifest(tmp_path)

    def unexpected_client():
        raise AssertionError("invalid manifest must not create a client")

    monkeypatch.setattr(cli, "_build_client", unexpected_client)
    exit_code = cli.main(
        [
            "evaluate",
            str(manifest),
            "--output-dir",
            str(tmp_path / "results"),
            "--max-audio-minutes",
            "0.001",
        ]
    )

    assert exit_code == 2
    assert "API는 호출되지 않았습니다" in capsys.readouterr().err


def test_evaluation_writes_outputs_corpus_strata_and_provenance(tmp_path):
    write_wav(tmp_path / "a.wav", 0.1)
    write_wav(tmp_path / "b.wav", 0.2)
    write_wav(tmp_path / "c.wav", 0.3)
    (tmp_path / "a.txt").write_text("가나", encoding="utf-8")
    (tmp_path / "b.txt").write_text("다라", encoding="utf-8")
    (tmp_path / "c.txt").write_text("마바사아", encoding="utf-8")
    manifest_path = tmp_path / "manifest.csv"
    write_manifest(
        manifest_path,
        [
            manifest_row("a", audio_path="a.wav", reference_path="a.txt"),
            manifest_row(
                "b",
                audio_path="b.wav",
                reference_path="b.txt",
                session_id="session-2",
                stratum="noisy",
            ),
            manifest_row(
                "c",
                audio_path="c.wav",
                reference_path="c.txt",
                session_id="session-3",
                stratum="noisy",
            ),
        ],
    )
    manifest = load_manifest(manifest_path)

    class FakeClient:
        def __init__(self):
            self.calls = []

        def transcribe(self, audio_path, **kwargs):
            self.calls.append((Path(audio_path), kwargs))
            message = {"a.wav": "가나", "b.wav": "다", "c.wav": ""}[Path(audio_path).name]
            return completed_response(message, f"job-{len(self.calls)}")

    client = FakeClient()
    progress = []
    output_dir = tmp_path / "results"
    summary = evaluate_manifest(
        client,
        manifest,
        output_dir,
        poll_interval=0.25,
        timeout=12,
        progress=lambda *values: progress.append(values),
    )

    assert [call[0].name for call in client.calls] == ["a.wav", "b.wav", "c.wav"]
    assert client.calls[0][1] == {
        "config": DEFAULT_TRANSCRIBE_CONFIG,
        "poll_interval": 0.25,
        "timeout": 12,
    }
    assert progress == [(1, 3, "a"), (2, 3, "b"), (3, 3, "c")]
    assert summary["sample_count"] == 3
    assert summary["total_audio_seconds"] == pytest.approx(0.6)
    assert summary["manifest_sha256"] == expected_file_sha256(manifest_path)
    assert (
        summary["config_sha256"]
        == "a74691f4d02c973986c022804e03b69c0345ab3ed130b4e13e9b35c3dabd355d"
    )
    assert summary["corpus"]["cer"] == pytest.approx(5 / 8)
    assert summary["strata"]["clean"]["cer"] == 0
    assert summary["strata"]["noisy"]["cer"] == pytest.approx(5 / 6)
    assert summary["strata"]["noisy"]["sample_count"] == 2
    assert summary["samples"][1]["session_id"] == "session-2"
    assert summary["samples"][1]["stratum"] == "noisy"
    assert summary["samples"][2]["deletions"] == 4
    assert summary["samples"][0]["audio_sha256"] == expected_file_sha256(tmp_path / "a.wav")
    assert summary["samples"][1]["reference_sha256"] == expected_file_sha256(tmp_path / "b.txt")
    assert json.loads((output_dir / "summary.json").read_text(encoding="utf-8")) == summary
    for sample_id in ("a", "b", "c"):
        assert {path.name for path in (output_dir / sample_id).iterdir()} == {
            "response.json",
            "transcript.txt",
            "transcript.srt",
            "metrics.json",
        }


def test_evaluation_transcribes_every_time(tmp_path):
    manifest = load_manifest(prepare_single_manifest(tmp_path))

    class CountingClient:
        def __init__(self):
            self.calls = 0

        def transcribe(self, *args, **kwargs):
            self.calls += 1
            return completed_response(job_id=f"job-{self.calls}")

    client = CountingClient()
    evaluate_manifest(client, manifest, tmp_path / "results")
    evaluate_manifest(client, manifest, tmp_path / "results")

    assert client.calls == 2


def test_transcribe_writes_requested_transcript_formats(tmp_path, monkeypatch):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")

    class FakeClient:
        def __init__(self):
            self.calls = []

        def transcribe(self, audio_path, **kwargs):
            self.calls.append((audio_path, kwargs))
            return completed_response()

    client = FakeClient()
    monkeypatch.setattr(cli, "_build_client", lambda: client)

    for output_format, expect_txt, expect_srt in (
        ("txt", True, False),
        ("srt", False, True),
        ("all", True, True),
    ):
        output = tmp_path / output_format
        assert (
            cli.main(
                [
                    "transcribe",
                    str(audio),
                    "--format",
                    output_format,
                    "--output-dir",
                    str(output),
                    "--poll-interval",
                    "0.5",
                    "--timeout",
                    "20",
                ]
            )
            == 0
        )
        assert (output / "response.json").is_file()
        assert (output / "transcript.txt").exists() is expect_txt
        assert (output / "transcript.srt").exists() is expect_srt
        assert not (output / "metrics.json").exists()

    assert len(client.calls) == 3
    assert all(
        call
        == (
            audio,
            {
                "config": DEFAULT_TRANSCRIBE_CONFIG,
                "poll_interval": 0.5,
                "timeout": 20.0,
            },
        )
        for call in client.calls
    )


def test_transcribe_reference_writes_cer_metrics(tmp_path, monkeypatch, capsys):
    audio = tmp_path / "sample.wav"
    reference = tmp_path / "sample.txt"
    output = tmp_path / "output"
    audio.write_bytes(b"audio")
    reference.write_text("안녕하세요", encoding="utf-8")

    class FakeClient:
        def transcribe(self, *args, **kwargs):
            return completed_response()

    monkeypatch.setattr(cli, "_build_client", lambda: FakeClient())
    exit_code = cli.main(
        [
            "transcribe",
            str(audio),
            "--output-dir",
            str(output),
            "--reference",
            str(reference),
        ]
    )

    assert exit_code == 0
    assert json.loads((output / "metrics.json").read_text(encoding="utf-8"))["cer"] == 0
    assert "CER: 0.00%" in capsys.readouterr().out


def test_transcribe_rejects_invalid_inputs_without_partial_outputs(tmp_path, monkeypatch):
    def unexpected_client():
        raise AssertionError("invalid input must not create a client")

    monkeypatch.setattr(cli, "_build_client", unexpected_client)
    assert (
        cli.main(
            [
                "transcribe",
                str(tmp_path / "missing.wav"),
                "--output-dir",
                str(tmp_path / "output"),
            ]
        )
        == 2
    )

    audio = tmp_path / "sample.wav"
    reference = tmp_path / "sample.txt"
    audio.write_bytes(b"audio")
    reference.write_text("n/ !!!", encoding="utf-8")
    assert (
        cli.main(
            [
                "transcribe",
                str(audio),
                "--output-dir",
                str(tmp_path / "output"),
                "--reference",
                str(reference),
            ]
        )
        == 2
    )

    class MalformedClient:
        def transcribe(self, *args, **kwargs):
            return {"results": {"utterances": [{"msg": "문장"}]}}

    malformed_output = tmp_path / "malformed-output"
    monkeypatch.setattr(cli, "_build_client", lambda: MalformedClient())
    assert (
        cli.main(
            [
                "transcribe",
                str(audio),
                "--format",
                "all",
                "--output-dir",
                str(malformed_output),
            ]
        )
        == 2
    )
    assert not malformed_output.exists()


def test_evaluate_cli_runs_manifest_and_prints_summary(tmp_path, monkeypatch, capsys):
    manifest = prepare_single_manifest(tmp_path)
    output = tmp_path / "results"

    class FakeClient:
        def transcribe(self, *args, **kwargs):
            return completed_response()

    monkeypatch.setattr(cli, "_build_client", lambda: FakeClient())

    exit_code = cli.main(
        [
            "evaluate",
            str(manifest),
            "--output-dir",
            str(output),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "표본: 1개" in captured.out
    assert "Corpus CER: 0.00%" in captured.out
    assert "[1/1] sample-1" in captured.err
    assert json.loads((output / "summary.json").read_text(encoding="utf-8"))["sample_count"] == 1
