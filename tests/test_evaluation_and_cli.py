from __future__ import annotations

import hashlib
import json
import tarfile
import wave
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
import soundfile as sf

from rtzr_stt import cli
from rtzr_stt.config import DEFAULT_TRANSCRIBE_CONFIG
from rtzr_stt.evaluation import (
    FLEURS_AUDIO_ARCHIVE,
    FLEURS_LANGUAGE,
    FLEURS_METADATA_FILE,
    FLEURS_REPO_ID,
    FLEURS_REVISION,
    FLEURS_SPLIT,
    EvaluationDataError,
    FleursSample,
    PreparedEvaluation,
    evaluate_fleurs,
    prepare_fleurs_evaluation,
)


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


def float_wav_bytes(
    frames: int,
    *,
    sample_rate: int = 16_000,
    channels: int = 1,
    subtype: str = "FLOAT",
    amplitude: float = 0.25,
) -> bytes:
    destination = BytesIO()
    amplitude = float(amplitude)
    samples: list[float] | list[list[float]]
    if channels == 1:
        samples = [amplitude if index % 2 else -amplitude for index in range(frames)]
    else:
        samples = [[amplitude if index % 2 else -amplitude] * channels for index in range(frames)]
    sf.write(destination, samples, sample_rate, format="WAV", subtype=subtype)
    return destination.getvalue()


def metadata_row(
    index: int,
    *,
    filename: str | None = None,
    transcription: str | None = None,
    num_samples: int | str = 1_600,
) -> list[str]:
    return [
        f"sentence-{index}",
        filename or f"{index}.wav",
        "raw transcription",
        transcription if transcription is not None else f"정답 {index}",
        "7",
        str(num_samples),
        "MALE",
    ]


def write_metadata(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        for row in rows:
            destination.write("\t".join(row) + "\n")


def write_archive(path: Path, entries: list[tuple[str, bytes | None]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in entries:
            member = tarfile.TarInfo(name)
            if content is None:
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            else:
                member.size = len(content)
                archive.addfile(member, BytesIO(content))


def make_fleurs_files(
    tmp_path: Path,
    rows: list[list[str]],
    *,
    entries: list[tuple[str, bytes | None]] | None = None,
) -> tuple[Path, Path, dict[str, bytes]]:
    metadata = tmp_path / "dev.tsv"
    archive = tmp_path / "dev.tar.gz"
    write_metadata(metadata, rows)
    source_audio: dict[str, bytes] = {}
    if entries is None:
        entries = []
        for row in rows:
            content = float_wav_bytes(int(row[5]))
            source_audio[row[1]] = content
            entries.append((f"dev/{row[1]}", content))
    write_archive(archive, entries)
    return metadata, archive, source_audio


def fake_downloader(metadata: Path, archive: Path, calls: list[dict] | None = None):
    def download(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        if kwargs["filename"] == FLEURS_METADATA_FILE:
            return str(metadata)
        if kwargs["filename"] == FLEURS_AUDIO_ARCHIVE:
            return str(archive)
        raise AssertionError(f"unexpected file: {kwargs['filename']}")

    return download


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_prepare_uses_pinned_cache_files_first_rows_and_pcm16(tmp_path):
    rows = [
        metadata_row(1, transcription='첫 "문장"', num_samples=1_600),
        metadata_row(2, transcription="둘째!", num_samples=3_200),
        metadata_row(3, transcription="선택 안 됨", num_samples=800),
    ]
    metadata, archive, source_audio = make_fleurs_files(tmp_path, rows)
    calls: list[dict] = []
    temporary_audio: list[Path] = []

    with prepare_fleurs_evaluation(
        2,
        downloader=fake_downloader(metadata, archive, calls),
    ) as prepared:
        assert [sample.sample_id for sample in prepared.samples] == ["1", "2"]
        assert [sample.reference for sample in prepared.samples] == ['첫 "문장"', "둘째!"]
        assert prepared.total_audio_seconds == pytest.approx(0.3)
        assert prepared.metadata_sha256 == sha256_bytes(metadata.read_bytes())
        temporary_audio = [sample.audio_path for sample in prepared.samples]
        for sample, expected_frames in zip(prepared.samples, (1_600, 3_200), strict=True):
            assert sample.audio_path.is_file()
            with wave.open(str(sample.audio_path), "rb") as converted:
                assert converted.getframerate() == 16_000
                assert converted.getnchannels() == 1
                assert converted.getsampwidth() == 2
                assert converted.getnframes() == expected_frames
                assert any(converted.readframes(expected_frames))
            converted_samples, _ = sf.read(sample.audio_path, dtype="int16")
            assert set(converted_samples.tolist()) == {-8192, 8192}
            assert sample.source_audio_sha256 == sha256_bytes(source_audio[sample.source_filename])
            assert sample.uploaded_audio_sha256 == sha256_bytes(sample.audio_path.read_bytes())
            assert sample.reference_sha256 == sha256_bytes(sample.reference.encode())

    assert all(not path.exists() for path in temporary_audio)
    assert calls == [
        {
            "repo_id": FLEURS_REPO_ID,
            "filename": FLEURS_METADATA_FILE,
            "repo_type": "dataset",
            "revision": FLEURS_REVISION,
        },
        {
            "repo_id": FLEURS_REPO_ID,
            "filename": FLEURS_AUDIO_ARCHIVE,
            "repo_type": "dataset",
            "revision": FLEURS_REVISION,
        },
    ]


@pytest.mark.parametrize(
    ("rows", "sample_count", "message", "expected_calls"),
    [
        ([metadata_row(1)], 0, "0보다 큰 정수", []),
        ([metadata_row(1)], True, "0보다 큰 정수", []),
        ([metadata_row(1)], 2, "보다 많습니다", [FLEURS_METADATA_FILE]),
        (
            [metadata_row(1, transcription="")],
            1,
            "transcription이 비었습니다",
            [FLEURS_METADATA_FILE],
        ),
        (
            [metadata_row(1, num_samples="invalid")],
            1,
            "정수가 아닙니다",
            [FLEURS_METADATA_FILE],
        ),
        ([metadata_row(1, num_samples=0)], 1, "0보다 커야", [FLEURS_METADATA_FILE]),
        (
            [["", "1.wav", "raw", "정답", "7", "1600", "MALE"]],
            1,
            "문장 ID가 비었습니다",
            [FLEURS_METADATA_FILE],
        ),
        (
            [["sentence", "1.wav", "raw", "정답", "7", "1600"]],
            1,
            "열 개수가 7개가 아닙니다",
            [FLEURS_METADATA_FILE],
        ),
        (
            [metadata_row(1, filename="7.wav"), metadata_row(2, filename="7.wav")],
            1,
            "중복",
            [FLEURS_METADATA_FILE],
        ),
        ([metadata_row(1, filename="..wav")], 1, "숫자 WAV", [FLEURS_METADATA_FILE]),
        ([metadata_row(1, filename="...wav")], 1, "숫자 WAV", [FLEURS_METADATA_FILE]),
        ([metadata_row(1, filename=r"a\b.wav")], 1, "숫자 WAV", [FLEURS_METADATA_FILE]),
        (
            [metadata_row(1, filename="summary.json.wav")],
            1,
            "숫자 WAV",
            [FLEURS_METADATA_FILE],
        ),
    ],
)
def test_prepare_rejects_invalid_metadata_before_archive(
    tmp_path,
    rows,
    sample_count,
    message,
    expected_calls,
):
    metadata = tmp_path / "dev.tsv"
    write_metadata(metadata, rows)
    calls = []

    def metadata_only_downloader(**kwargs):
        calls.append(kwargs["filename"])
        if kwargs["filename"] != FLEURS_METADATA_FILE:
            raise AssertionError("archive must not be requested")
        return str(metadata)

    with pytest.raises(EvaluationDataError, match=message):
        with prepare_fleurs_evaluation(sample_count, downloader=metadata_only_downloader):
            pass
    assert calls == expected_calls


def test_prepare_wraps_invalid_utf8_metadata(tmp_path):
    metadata = tmp_path / "dev.tsv"
    metadata.write_bytes(b"\xff")

    def metadata_only_downloader(**kwargs):
        assert kwargs["filename"] == FLEURS_METADATA_FILE
        return str(metadata)

    with pytest.raises(EvaluationDataError, match="메타데이터를 읽을 수 없습니다"):
        with prepare_fleurs_evaluation(1, downloader=metadata_only_downloader):
            pass


@pytest.mark.parametrize("failed_file", [FLEURS_METADATA_FILE, FLEURS_AUDIO_ARCHIVE])
def test_prepare_wraps_download_errors(tmp_path, failed_file):
    metadata = tmp_path / "dev.tsv"
    write_metadata(metadata, [metadata_row(1)])

    def failing_downloader(**kwargs):
        if kwargs["filename"] == failed_file:
            raise OSError("download failed")
        return str(metadata)

    with pytest.raises(EvaluationDataError, match="다운로드에 실패했습니다"):
        with prepare_fleurs_evaluation(1, downloader=failing_downloader):
            pass


def test_prepare_rejects_corrupt_archive(tmp_path):
    metadata = tmp_path / "dev.tsv"
    archive = tmp_path / "dev.tar.gz"
    write_metadata(metadata, [metadata_row(1)])
    archive.write_bytes(b"not a gzip archive")

    with pytest.raises(EvaluationDataError, match="압축파일을 읽을 수 없습니다"):
        with prepare_fleurs_evaluation(1, downloader=fake_downloader(metadata, archive)):
            pass


def test_duration_limit_fails_before_archive_download(tmp_path):
    metadata = tmp_path / "dev.tsv"
    write_metadata(metadata, [metadata_row(1, num_samples=14_400_001)])
    calls = []

    def metadata_only_downloader(**kwargs):
        calls.append(kwargs["filename"])
        if kwargs["filename"] != FLEURS_METADATA_FILE:
            raise AssertionError("archive must not be requested")
        return str(metadata)

    with pytest.raises(EvaluationDataError, match="API는 호출되지 않았습니다"):
        with prepare_fleurs_evaluation(1, downloader=metadata_only_downloader):
            pass
    assert calls == [FLEURS_METADATA_FILE]


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([("../escape.wav", b"audio")], "안전하지 않은 경로"),
        ([("dev/readme.txt", b"text")], "예상하지 않은 파일"),
        ([], "표본이 없습니다"),
    ],
)
def test_prepare_rejects_unsafe_missing_or_non_wav_tar_members(
    tmp_path,
    entries,
    message,
):
    rows = [metadata_row(1)]
    metadata, archive, _ = make_fleurs_files(tmp_path, rows, entries=entries)

    with pytest.raises(EvaluationDataError, match=message):
        with prepare_fleurs_evaluation(1, downloader=fake_downloader(metadata, archive)):
            pass


def test_prepare_rejects_duplicate_tar_member(tmp_path):
    rows = [metadata_row(1)]
    content = float_wav_bytes(1_600)
    metadata, archive, _ = make_fleurs_files(
        tmp_path,
        rows,
        entries=[("dev/1.wav", content), ("dev/1.wav", content)],
    )

    with pytest.raises(EvaluationDataError, match="중복된 경로"):
        with prepare_fleurs_evaluation(1, downloader=fake_downloader(metadata, archive)):
            pass


@pytest.mark.parametrize(
    ("audio", "num_samples", "message"),
    [
        (lambda: float_wav_bytes(1_600, sample_rate=8_000), 1_600, "16000Hz"),
        (lambda: float_wav_bytes(1_600, channels=2), 1_600, "mono"),
        (lambda: float_wav_bytes(800), 1_600, "frame 수"),
        (lambda: float_wav_bytes(1_600, subtype="PCM_16"), 1_600, "float WAV"),
        (lambda: float_wav_bytes(1_600, amplitude=0), 1_600, "음성 신호"),
        (lambda: float_wav_bytes(1_600, amplitude=float("nan")), 1_600, "유한하지 않은"),
    ],
)
def test_prepare_rejects_invalid_wav_structure(tmp_path, audio, num_samples, message):
    rows = [metadata_row(1, num_samples=num_samples)]
    metadata, archive, _ = make_fleurs_files(
        tmp_path,
        rows,
        entries=[("dev/1.wav", audio())],
    )

    with pytest.raises(EvaluationDataError, match=message):
        with prepare_fleurs_evaluation(1, downloader=fake_downloader(metadata, archive)):
            pass


def test_evaluation_writes_manifest_outputs_exact_corpus_and_provenance(tmp_path):
    rows = [
        metadata_row(1, transcription="가 나", num_samples=1_600),
        metadata_row(2, transcription="둘!", num_samples=3_200),
    ]
    metadata, archive, _ = make_fleurs_files(tmp_path, rows)

    class FakeClient:
        def __init__(self):
            self.calls = []

        def transcribe(self, audio_path, **kwargs):
            self.calls.append((Path(audio_path), kwargs))
            messages = {"1.wav": "가나", "2.wav": "둘!"}
            return completed_response(messages[Path(audio_path).name], f"job-{len(self.calls)}")

    output = tmp_path / "results"
    progress = []
    client = FakeClient()
    with prepare_fleurs_evaluation(
        2,
        downloader=fake_downloader(metadata, archive),
    ) as prepared:
        summary = evaluate_fleurs(
            client,
            prepared,
            output,
            poll_interval=0.25,
            timeout=12,
            progress=lambda *values: progress.append(values),
        )

    assert progress == [(1, 2, "1"), (2, 2, "2")]
    assert all(
        call[1]
        == {
            "config": DEFAULT_TRANSCRIBE_CONFIG,
            "poll_interval": 0.25,
            "timeout": 12,
        }
        for call in client.calls
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset"]["repo_id"] == FLEURS_REPO_ID
    assert manifest["dataset"]["revision"] == FLEURS_REVISION
    assert manifest["dataset"]["split"] == FLEURS_SPLIT
    assert manifest["dataset"]["language"] == FLEURS_LANGUAGE
    assert manifest["selection"] == {"method": "first_rows", "sample_count": 2}
    assert manifest["samples"][0]["reference"] == "가 나"
    assert manifest["samples"][0]["source_audio_sha256"]
    assert manifest["samples"][0]["uploaded_audio_sha256"]
    assert summary["sample_count"] == 2
    assert summary["total_audio_seconds"] == pytest.approx(0.3)
    assert summary["corpus"]["deletions"] == 1
    assert summary["corpus"]["reference_characters"] == 5
    assert summary["corpus"]["cer"] == pytest.approx(1 / 5)
    assert "strata" not in summary
    assert "hypothesis" not in summary["samples"][0]
    assert summary["manifest_sha256"] == sha256_bytes((output / "manifest.json").read_bytes())
    assert json.loads((output / "summary.json").read_text(encoding="utf-8")) == summary
    metrics = json.loads((output / "1" / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["reference"] == "가 나"
    assert metrics["hypothesis"] == "가나"
    assert "normalized_reference" not in metrics
    for sample_id in ("1", "2"):
        assert {path.name for path in (output / sample_id).iterdir()} == {
            "response.json",
            "transcript.txt",
            "transcript.srt",
            "metrics.json",
        }


def make_prepared(tmp_path: Path, count: int = 1, reference: str = "안녕하세요"):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    samples = tuple(
        FleursSample(
            sample_id=str(index),
            dataset_id=f"sentence-{index}",
            source_filename=f"{index}.wav",
            audio_path=audio,
            reference=reference,
            num_samples=160,
            duration_seconds=0.01,
            source_audio_sha256="a" * 64,
            uploaded_audio_sha256="b" * 64,
            reference_sha256=sha256_bytes(reference.encode()),
        )
        for index in range(1, count + 1)
    )
    return PreparedEvaluation(
        samples=samples,
        total_audio_seconds=count * 0.01,
        metadata_sha256="c" * 64,
    )


def test_evaluation_is_stateless_and_transcribes_every_time_in_new_directories(tmp_path):
    prepared = make_prepared(tmp_path)

    class CountingClient:
        def __init__(self):
            self.calls = 0

        def transcribe(self, *args, **kwargs):
            self.calls += 1
            return completed_response(job_id=f"job-{self.calls}")

    client = CountingClient()
    evaluate_fleurs(client, prepared, tmp_path / "results-1")
    evaluate_fleurs(client, prepared, tmp_path / "results-2")
    assert client.calls == 2


def test_evaluation_rejects_nonempty_output_before_transcription(tmp_path):
    prepared = make_prepared(tmp_path)
    output = tmp_path / "results"
    output.mkdir()
    (output / "old.txt").write_text("old", encoding="utf-8")

    class UnexpectedClient:
        def transcribe(self, *args, **kwargs):
            raise AssertionError("nonempty output must fail before transcription")

    with pytest.raises(ValueError, match="비어 있지 않습니다"):
        evaluate_fleurs(UnexpectedClient(), prepared, output)


@pytest.mark.parametrize(
    "sample_ids",
    [
        ("",),
        ("..",),
        ("1", "1"),
    ],
)
def test_evaluation_rejects_unsafe_or_duplicate_prepared_ids_before_output(tmp_path, sample_ids):
    base_sample = make_prepared(tmp_path).samples[0]
    prepared = PreparedEvaluation(
        samples=tuple(replace(base_sample, sample_id=sample_id) for sample_id in sample_ids),
        total_audio_seconds=0.01 * len(sample_ids),
        metadata_sha256="c" * 64,
    )
    output = tmp_path / "results"

    class UnexpectedClient:
        def transcribe(self, *args, **kwargs):
            raise AssertionError("invalid prepared IDs must fail before transcription")

    with pytest.raises(EvaluationDataError, match="숫자가 아닙니다|중복되었습니다"):
        evaluate_fleurs(UnexpectedClient(), prepared, output)
    assert not output.exists()


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


def test_transcribe_reference_strips_only_outer_whitespace(tmp_path, monkeypatch, capsys):
    audio = tmp_path / "sample.wav"
    reference = tmp_path / "sample.txt"
    output = tmp_path / "output"
    audio.write_bytes(b"audio")
    reference.write_text(" \n안녕하세요\n ", encoding="utf-8")

    class FakeClient:
        def transcribe(self, *args, **kwargs):
            return completed_response()

    monkeypatch.setattr(cli, "_build_client", lambda: FakeClient())
    assert (
        cli.main(
            [
                "transcribe",
                str(audio),
                "--output-dir",
                str(output),
                "--reference",
                str(reference),
            ]
        )
        == 0
    )
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["cer"] == 0
    assert metrics["reference"] == "안녕하세요"
    assert "CER: 0.00%" in capsys.readouterr().out


def test_transcribe_reference_keeps_internal_formatting_as_errors(tmp_path, monkeypatch):
    audio = tmp_path / "sample.wav"
    reference = tmp_path / "sample.txt"
    output = tmp_path / "output"
    audio.write_bytes(b"audio")
    reference.write_text("안녕 하세요!", encoding="utf-8")

    class FakeClient:
        def transcribe(self, *args, **kwargs):
            return completed_response("안녕하세요")

    monkeypatch.setattr(cli, "_build_client", lambda: FakeClient())
    assert (
        cli.main(
            [
                "transcribe",
                str(audio),
                "--output-dir",
                str(output),
                "--reference",
                str(reference),
            ]
        )
        == 0
    )
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["deletions"] == 2
    assert metrics["cer"] > 0


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
    reference.write_text(" \n ", encoding="utf-8")
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


def test_cli_rejects_nonempty_output_before_client_or_dataset_preparation(
    tmp_path, monkeypatch, capsys
):
    audio = tmp_path / "sample.wav"
    output = tmp_path / "results"
    audio.write_bytes(b"audio")
    output.mkdir()
    (output / "old.txt").write_text("old", encoding="utf-8")

    def unexpected_call(*args, **kwargs):
        raise AssertionError("nonempty output must fail before external work")

    monkeypatch.setattr(cli, "_build_client", unexpected_call)
    monkeypatch.setattr(cli, "prepare_fleurs_evaluation", unexpected_call)

    assert cli.main(["transcribe", str(audio), "--output-dir", str(output)]) == 2
    assert cli.main(["evaluate", "--output-dir", str(output)]) == 2
    assert capsys.readouterr().err.count("비어 있지 않습니다") == 2


def test_evaluate_limit_failure_happens_before_client_creation(tmp_path, monkeypatch, capsys):
    @contextmanager
    def failed_prepare(sample_count):
        assert sample_count == 1
        raise EvaluationDataError("고정 제한을 초과했습니다. API는 호출되지 않았습니다.")
        yield

    def unexpected_client():
        raise AssertionError("invalid FLEURS input must not create a client")

    monkeypatch.setattr(cli, "prepare_fleurs_evaluation", failed_prepare)
    monkeypatch.setattr(cli, "_build_client", unexpected_client)
    assert cli.main(["evaluate", "--output-dir", str(tmp_path / "results")]) == 2
    assert "API는 호출되지 않았습니다" in capsys.readouterr().err


def test_evaluate_cli_passes_options_and_prints_summary(tmp_path, monkeypatch, capsys):
    prepared = make_prepared(tmp_path, count=2)

    @contextmanager
    def fake_prepare(sample_count):
        assert sample_count == 2
        yield prepared

    class FakeClient:
        def __init__(self):
            self.calls = []

        def transcribe(self, audio_path, **kwargs):
            self.calls.append((audio_path, kwargs))
            return completed_response(job_id=f"job-{len(self.calls)}")

    client = FakeClient()
    monkeypatch.setattr(cli, "prepare_fleurs_evaluation", fake_prepare)
    monkeypatch.setattr(cli, "_build_client", lambda: client)
    output = tmp_path / "results"

    assert (
        cli.main(
            [
                "evaluate",
                "--samples",
                "2",
                "--output-dir",
                str(output),
                "--poll-interval",
                "0.25",
                "--timeout",
                "12",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert len(client.calls) == 2
    assert all(
        kwargs
        == {
            "config": DEFAULT_TRANSCRIBE_CONFIG,
            "poll_interval": 0.25,
            "timeout": 12.0,
        }
        for _, kwargs in client.calls
    )
    assert "표본: 2개" in captured.out
    assert "Corpus CER: 0.00%" in captured.out
    assert "[2/2] 2" in captured.err
    assert json.loads((output / "summary.json").read_text(encoding="utf-8"))["sample_count"] == 2
