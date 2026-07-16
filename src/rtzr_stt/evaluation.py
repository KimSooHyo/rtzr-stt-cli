from __future__ import annotations

import csv
import hashlib
import math
import tarfile
import tempfile
import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

import soundfile as sf
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import HfHubHTTPError

from rtzr_stt.api import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_WAIT_TIMEOUT_SECONDS,
    RTZRClient,
    validate_wait_options,
)
from rtzr_stt.config import DEFAULT_TRANSCRIBE_CONFIG, config_sha256
from rtzr_stt.formatters import hypothesis_text, transcript_srt, transcript_text
from rtzr_stt.io import (
    file_sha256,
    validate_empty_output_directory,
    write_json_atomic,
    write_text_atomic,
)
from rtzr_stt.metrics import character_error_metrics, corpus_character_error_metrics

FLEURS_REPO_ID = "google/fleurs"
FLEURS_REVISION = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"
FLEURS_LANGUAGE = "ko_kr"
FLEURS_SPLIT = "validation"
FLEURS_LICENSE = "CC-BY-4.0"
FLEURS_METADATA_FILE = "data/ko_kr/dev.tsv"
FLEURS_AUDIO_ARCHIVE = "data/ko_kr/audio/dev.tar.gz"
FLEURS_ARCHIVE_DIRECTORY = "dev"
FLEURS_SAMPLE_RATE = 16_000
MAX_EVALUATION_SECONDS = 15 * 60
DEFAULT_SAMPLE_COUNT = 1


class EvaluationDataError(ValueError):
    pass


@dataclass(frozen=True)
class _FleursMetadataRow:
    dataset_id: str
    source_filename: str
    reference: str
    num_samples: int

    @property
    def sample_id(self) -> str:
        return PurePosixPath(self.source_filename).stem


@dataclass(frozen=True)
class FleursSample:
    sample_id: str
    dataset_id: str
    source_filename: str
    audio_path: Path
    reference: str
    num_samples: int
    duration_seconds: float
    source_audio_sha256: str
    uploaded_audio_sha256: str
    reference_sha256: str


@dataclass(frozen=True)
class PreparedEvaluation:
    samples: tuple[FleursSample, ...]
    total_audio_seconds: float
    metadata_sha256: str


def _bytes_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _text_sha256(content: str) -> str:
    return _bytes_sha256(content.encode("utf-8"))


def _validate_sample_count(sample_count: int) -> None:
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count <= 0:
        raise EvaluationDataError("samples는 0보다 큰 정수여야 합니다.")


def wav_duration_seconds(path: str | Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            frame_rate = audio.getframerate()
            if frame_rate <= 0:
                raise EvaluationDataError(f"WAV sample rate가 올바르지 않습니다: {path}")
            frame_count = audio.getnframes()
            duration_seconds = frame_count / frame_rate
            if duration_seconds <= 0:
                raise EvaluationDataError(f"WAV 오디오 길이는 0초보다 커야 합니다: {path}")

            bytes_per_frame = audio.getnchannels() * audio.getsampwidth()
            if bytes_per_frame <= 0:
                raise EvaluationDataError(f"WAV 프레임 형식이 올바르지 않습니다: {path}")
            expected_bytes = frame_count * bytes_per_frame
            actual_bytes = 0
            remaining_frames = frame_count
            while remaining_frames > 0:
                chunk = audio.readframes(min(8_192, remaining_frames))
                if not chunk:
                    break
                actual_bytes += len(chunk)
                complete_frames, partial_bytes = divmod(len(chunk), bytes_per_frame)
                remaining_frames -= complete_frames
                if partial_bytes:
                    break
            if actual_bytes != expected_bytes:
                raise EvaluationDataError(
                    f"WAV 오디오 데이터가 헤더의 프레임 수와 다릅니다: {path}"
                )
            return duration_seconds
    except (EOFError, OSError, wave.Error) as exc:
        raise EvaluationDataError(f"WAV 파일을 읽을 수 없습니다: {path}") from exc


def _load_metadata(path: Path, sample_count: int) -> tuple[_FleursMetadataRow, ...]:
    _validate_sample_count(sample_count)
    rows: list[_FleursMetadataRow] = []
    seen_sample_ids: set[str] = set()
    try:
        with path.open("r", encoding="utf-8", newline="") as source:
            reader = csv.reader(source, delimiter="\t", quoting=csv.QUOTE_NONE)
            for row_number, row in enumerate(reader, start=1):
                if len(row) != 7:
                    raise EvaluationDataError(
                        f"FLEURS 메타데이터 {row_number}행의 열 개수가 7개가 아닙니다."
                    )
                dataset_id, filename, _, transcription, _, num_samples_text, _ = row
                if not dataset_id.strip():
                    raise EvaluationDataError(
                        f"FLEURS 메타데이터 {row_number}행의 문장 ID가 비었습니다."
                    )
                filename_path = PurePosixPath(filename)
                if (
                    not filename
                    or filename_path.name != filename
                    or filename_path.suffix != ".wav"
                    or not filename_path.stem.isdigit()
                ):
                    raise EvaluationDataError(
                        f"FLEURS 메타데이터 {row_number}행의 숫자 WAV 파일명이 올바르지 않습니다."
                    )
                sample_id = filename_path.stem
                if sample_id in seen_sample_ids:
                    raise EvaluationDataError(f"FLEURS 표본 ID가 중복되었습니다: {sample_id}")
                seen_sample_ids.add(sample_id)
                if not transcription.strip():
                    raise EvaluationDataError(
                        f"FLEURS 메타데이터 {row_number}행의 transcription이 비었습니다."
                    )
                try:
                    num_samples = int(num_samples_text)
                except ValueError as exc:
                    raise EvaluationDataError(
                        f"FLEURS 메타데이터 {row_number}행의 sample 수가 정수가 아닙니다."
                    ) from exc
                if num_samples <= 0:
                    raise EvaluationDataError(
                        f"FLEURS 메타데이터 {row_number}행의 sample 수는 0보다 커야 합니다."
                    )
                rows.append(
                    _FleursMetadataRow(
                        dataset_id=dataset_id,
                        source_filename=filename,
                        reference=transcription,
                        num_samples=num_samples,
                    )
                )
    except (OSError, UnicodeError, csv.Error) as exc:
        raise EvaluationDataError("FLEURS 메타데이터를 읽을 수 없습니다.") from exc

    if sample_count > len(rows):
        raise EvaluationDataError(
            f"요청한 표본 {sample_count}개가 FLEURS validation {len(rows)}개보다 많습니다."
        )
    selected = tuple(rows[:sample_count])
    total_seconds = sum(row.num_samples for row in selected) / FLEURS_SAMPLE_RATE
    if total_seconds > MAX_EVALUATION_SECONDS + 1e-9:
        raise EvaluationDataError(
            f"선택한 오디오 {total_seconds:.2f}초가 고정 제한 "
            f"{MAX_EVALUATION_SECONDS:.2f}초를 초과합니다. API는 호출되지 않았습니다."
        )
    return selected


def _validate_tar_members(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise EvaluationDataError("FLEURS 오디오 압축파일에 안전하지 않은 경로가 있습니다.")
        if member.name in members:
            raise EvaluationDataError(
                f"FLEURS 오디오 압축파일에 중복된 경로가 있습니다: {member.name}"
            )
        if member.isfile() and (
            path.parent != PurePosixPath(FLEURS_ARCHIVE_DIRECTORY) or path.suffix.lower() != ".wav"
        ):
            raise EvaluationDataError(
                f"FLEURS 오디오 압축파일에 예상하지 않은 파일이 있습니다: {member.name}"
            )
        if not member.isfile() and not member.isdir():
            raise EvaluationDataError(
                f"FLEURS 오디오 압축파일에 일반 파일이 아닌 항목이 있습니다: {member.name}"
            )
        members[member.name] = member
    return members


def _convert_sample(
    archive: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    metadata: _FleursMetadataRow,
    destination: Path,
) -> FleursSample:
    member_name = f"{FLEURS_ARCHIVE_DIRECTORY}/{metadata.source_filename}"
    member = members.get(member_name)
    if member is None:
        raise EvaluationDataError(f"FLEURS 오디오 압축파일에 표본이 없습니다: {member_name}")
    if not member.isfile():
        raise EvaluationDataError(f"FLEURS 오디오 표본이 일반 파일이 아닙니다: {member_name}")
    extracted = archive.extractfile(member)
    if extracted is None:
        raise EvaluationDataError(f"FLEURS 오디오 표본을 읽을 수 없습니다: {member_name}")
    try:
        source_audio = extracted.read()
    except OSError as exc:
        raise EvaluationDataError(f"FLEURS 오디오 표본을 읽을 수 없습니다: {member_name}") from exc
    if not source_audio:
        raise EvaluationDataError(f"FLEURS 오디오 표본이 비었습니다: {member_name}")

    try:
        info = sf.info(BytesIO(source_audio))
        audio, sample_rate = sf.read(BytesIO(source_audio), dtype="float32", always_2d=True)
    except (OSError, RuntimeError) as exc:
        raise EvaluationDataError(f"FLEURS WAV를 해석할 수 없습니다: {member_name}") from exc
    if info.format != "WAV" or info.subtype != "FLOAT":
        raise EvaluationDataError(f"FLEURS 오디오가 float WAV가 아닙니다: {member_name}")
    if sample_rate != FLEURS_SAMPLE_RATE:
        raise EvaluationDataError(
            f"FLEURS WAV sample rate가 {FLEURS_SAMPLE_RATE}Hz가 아닙니다: {member_name}"
        )
    if audio.shape[1] != 1:
        raise EvaluationDataError(f"FLEURS WAV가 mono가 아닙니다: {member_name}")
    if audio.shape[0] != metadata.num_samples:
        raise EvaluationDataError(f"FLEURS WAV frame 수가 메타데이터와 다릅니다: {member_name}")
    source_minimum = float(audio.min())
    source_maximum = float(audio.max())
    if not math.isfinite(source_minimum) or not math.isfinite(source_maximum):
        raise EvaluationDataError(f"FLEURS WAV에 유한하지 않은 sample이 있습니다: {member_name}")
    if source_minimum == 0 and source_maximum == 0:
        raise EvaluationDataError(f"FLEURS WAV에 유효한 음성 신호가 없습니다: {member_name}")

    output_path = destination / f"{metadata.sample_id}.wav"
    try:
        sf.write(output_path, audio[:, 0], sample_rate, format="WAV", subtype="PCM_16")
        converted_info = sf.info(output_path)
        converted_audio, _ = sf.read(output_path, dtype="int16", always_2d=True)
    except (OSError, RuntimeError) as exc:
        raise EvaluationDataError(f"PCM16 WAV를 만들 수 없습니다: {member_name}") from exc
    if (
        converted_info.format != "WAV"
        or converted_info.subtype != "PCM_16"
        or converted_info.samplerate != FLEURS_SAMPLE_RATE
        or converted_info.channels != 1
        or converted_info.frames != metadata.num_samples
    ):
        raise EvaluationDataError(f"변환한 WAV 구조가 올바르지 않습니다: {member_name}")
    if not converted_audio.any():
        raise EvaluationDataError(f"변환한 WAV에 유효한 음성 신호가 없습니다: {member_name}")
    duration_seconds = wav_duration_seconds(output_path)
    expected_duration = metadata.num_samples / FLEURS_SAMPLE_RATE
    if not math.isclose(duration_seconds, expected_duration, rel_tol=0, abs_tol=1e-9):
        raise EvaluationDataError(f"변환한 WAV 길이가 원본과 다릅니다: {member_name}")

    return FleursSample(
        sample_id=metadata.sample_id,
        dataset_id=metadata.dataset_id,
        source_filename=metadata.source_filename,
        audio_path=output_path,
        reference=metadata.reference,
        num_samples=metadata.num_samples,
        duration_seconds=duration_seconds,
        source_audio_sha256=_bytes_sha256(source_audio),
        uploaded_audio_sha256=file_sha256(output_path),
        reference_sha256=_text_sha256(metadata.reference),
    )


@contextmanager
def prepare_fleurs_evaluation(
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    *,
    downloader: Callable[..., str] = hf_hub_download,
) -> Iterator[PreparedEvaluation]:
    """Download, validate, and convert a fixed FLEURS subset before API use."""
    _validate_sample_count(sample_count)
    try:
        metadata_path = Path(
            downloader(
                repo_id=FLEURS_REPO_ID,
                filename=FLEURS_METADATA_FILE,
                repo_type="dataset",
                revision=FLEURS_REVISION,
            )
        )
    except (HfHubHTTPError, OSError) as exc:
        raise EvaluationDataError("FLEURS 메타데이터 다운로드에 실패했습니다.") from exc
    metadata_rows = _load_metadata(metadata_path, sample_count)

    try:
        archive_path = Path(
            downloader(
                repo_id=FLEURS_REPO_ID,
                filename=FLEURS_AUDIO_ARCHIVE,
                repo_type="dataset",
                revision=FLEURS_REVISION,
            )
        )
    except (HfHubHTTPError, OSError) as exc:
        raise EvaluationDataError("FLEURS 오디오 다운로드에 실패했습니다.") from exc

    with tempfile.TemporaryDirectory(prefix="rtzr-fleurs-") as temporary_name:
        destination = Path(temporary_name)
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                members = _validate_tar_members(archive)
                samples = tuple(
                    _convert_sample(archive, members, row, destination) for row in metadata_rows
                )
        except (OSError, tarfile.TarError) as exc:
            raise EvaluationDataError("FLEURS 오디오 압축파일을 읽을 수 없습니다.") from exc
        total_audio_seconds = sum(sample.duration_seconds for sample in samples)
        yield PreparedEvaluation(
            samples=samples,
            total_audio_seconds=total_audio_seconds,
            metadata_sha256=file_sha256(metadata_path),
        )


def _dataset_provenance(prepared: PreparedEvaluation) -> dict[str, Any]:
    return {
        "repo_id": FLEURS_REPO_ID,
        "revision": FLEURS_REVISION,
        "language": FLEURS_LANGUAGE,
        "split": FLEURS_SPLIT,
        "license": FLEURS_LICENSE,
        "metadata_file": FLEURS_METADATA_FILE,
        "audio_archive": FLEURS_AUDIO_ARCHIVE,
        "metadata_sha256": prepared.metadata_sha256,
    }


def _manifest_payload(prepared: PreparedEvaluation) -> dict[str, Any]:
    return {
        "dataset": _dataset_provenance(prepared),
        "selection": {
            "method": "first_rows",
            "sample_count": len(prepared.samples),
        },
        "total_audio_seconds": prepared.total_audio_seconds,
        "samples": [
            {
                "sample_id": sample.sample_id,
                "dataset_id": sample.dataset_id,
                "source_filename": sample.source_filename,
                "reference": sample.reference,
                "num_samples": sample.num_samples,
                "duration_seconds": sample.duration_seconds,
                "source_audio_sha256": sample.source_audio_sha256,
                "uploaded_audio_sha256": sample.uploaded_audio_sha256,
                "reference_sha256": sample.reference_sha256,
            }
            for sample in prepared.samples
        ],
    }


def _validate_prepared_samples(prepared: PreparedEvaluation) -> None:
    if not prepared.samples:
        raise EvaluationDataError("평가할 FLEURS 표본이 없습니다.")
    seen_ids: set[str] = set()
    for sample in prepared.samples:
        if not sample.sample_id.isdigit():
            raise EvaluationDataError(f"FLEURS 표본 ID가 숫자가 아닙니다: {sample.sample_id}")
        if sample.sample_id in seen_ids:
            raise EvaluationDataError(f"FLEURS 표본 ID가 중복되었습니다: {sample.sample_id}")
        seen_ids.add(sample.sample_id)


def evaluate_fleurs(
    client: RTZRClient,
    prepared: PreparedEvaluation,
    output_dir: str | Path,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Transcribe a prepared FLEURS subset and write exact CER results."""
    validate_wait_options(poll_interval, timeout)
    _validate_prepared_samples(prepared)
    destination = Path(output_dir)
    validate_empty_output_directory(destination)
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / "manifest.json"
    write_json_atomic(manifest_path, _manifest_payload(prepared))

    references: list[str] = []
    hypotheses: list[str] = []
    sample_results: list[dict[str, Any]] = []
    for index, sample in enumerate(prepared.samples, start=1):
        if progress is not None:
            progress(index, len(prepared.samples), sample.sample_id)
        response = client.transcribe(
            sample.audio_path,
            config=DEFAULT_TRANSCRIBE_CONFIG,
            poll_interval=poll_interval,
            timeout=timeout,
        )
        text_output = transcript_text(response)
        srt_output = transcript_srt(response)
        hypothesis = hypothesis_text(response)
        metrics = character_error_metrics(sample.reference, hypothesis)

        sample_dir = destination / sample.sample_id
        write_json_atomic(sample_dir / "response.json", response)
        write_text_atomic(sample_dir / "transcript.txt", text_output)
        write_text_atomic(sample_dir / "transcript.srt", srt_output)
        write_json_atomic(sample_dir / "metrics.json", metrics)

        references.append(sample.reference)
        hypotheses.append(hypothesis)
        sample_results.append(
            {
                "sample_id": sample.sample_id,
                "dataset_id": sample.dataset_id,
                "source_filename": sample.source_filename,
                "duration_seconds": sample.duration_seconds,
                "source_audio_sha256": sample.source_audio_sha256,
                "uploaded_audio_sha256": sample.uploaded_audio_sha256,
                "reference_sha256": sample.reference_sha256,
                **{
                    key: metrics[key]
                    for key in (
                        "cer",
                        "hits",
                        "substitutions",
                        "deletions",
                        "insertions",
                        "reference_characters",
                    )
                },
            }
        )

    summary: dict[str, Any] = {
        "dataset": _dataset_provenance(prepared),
        "selection": {
            "method": "first_rows",
            "sample_count": len(prepared.samples),
        },
        "sample_count": len(prepared.samples),
        "total_audio_seconds": prepared.total_audio_seconds,
        "manifest_sha256": file_sha256(manifest_path),
        "config_sha256": config_sha256(DEFAULT_TRANSCRIBE_CONFIG),
        "corpus": corpus_character_error_metrics(references, hypotheses),
        "samples": sample_results,
    }
    write_json_atomic(destination / "summary.json", summary)
    return summary
