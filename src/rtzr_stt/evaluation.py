from __future__ import annotations

import csv
import math
import wave
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Callable

from rtzr_stt.api import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_WAIT_TIMEOUT_SECONDS,
    RTZRClient,
    validate_wait_options,
)
from rtzr_stt.config import DEFAULT_TRANSCRIBE_CONFIG, config_sha256
from rtzr_stt.formatters import hypothesis_text, transcript_srt, transcript_text
from rtzr_stt.io import file_sha256, write_json_atomic, write_text_atomic
from rtzr_stt.metrics import (
    character_error_metrics,
    corpus_character_error_metrics,
    normalize_for_spelling_cer,
)

REQUIRED_COLUMNS = {
    "sample_id",
    "audio_path",
    "reference_path",
    "session_id",
    "stratum",
}
SAFE_SAMPLE_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ManifestSample:
    sample_id: str
    audio_path: Path
    reference_path: Path
    session_id: str
    stratum: str
    duration_seconds: float
    audio_sha256: str
    reference_sha256: str
    reference_text: str


@dataclass(frozen=True)
class ValidatedManifest:
    path: Path
    sha256: str
    samples: tuple[ManifestSample, ...]
    total_duration_seconds: float


def wav_duration_seconds(path: str | Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            frame_rate = audio.getframerate()
            if frame_rate <= 0:
                raise ManifestError(f"WAV sample rate가 올바르지 않습니다: {path}")
            frame_count = audio.getnframes()
            duration_seconds = frame_count / frame_rate
            if duration_seconds <= 0:
                raise ManifestError(f"WAV 오디오 길이는 0초보다 커야 합니다: {path}")

            bytes_per_frame = audio.getnchannels() * audio.getsampwidth()
            if bytes_per_frame <= 0:
                raise ManifestError(f"WAV 프레임 형식이 올바르지 않습니다: {path}")
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
                raise ManifestError(f"WAV 오디오 데이터가 헤더의 프레임 수와 다릅니다: {path}")
            return duration_seconds
    except (EOFError, OSError, wave.Error) as exc:
        raise ManifestError(f"WAV 파일을 읽을 수 없습니다: {path}") from exc


def _resolve_manifest_path(base: Path, value: str, field: str, row_number: int) -> Path:
    if not value.strip():
        raise ManifestError(f"{row_number}행의 {field}가 비었습니다.")
    path = (base / value).resolve()
    if not path.is_file():
        raise ManifestError(f"{row_number}행의 {field} 파일이 없습니다: {value}")
    return path


def _read_reference(path: Path, row_number: int, source_value: str) -> str:
    try:
        reference = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"{row_number}행의 정답을 읽을 수 없습니다: {source_value}") from exc
    except UnicodeError as exc:
        raise ManifestError(
            f"{row_number}행의 정답이 유효한 UTF-8이 아닙니다: {source_value}"
        ) from exc
    if not normalize_for_spelling_cer(reference):
        raise ManifestError(f"{row_number}행의 정답이 정규화 후 비었습니다: {source_value}")
    return reference


def load_manifest(
    manifest_path: str | Path,
    *,
    max_audio_minutes: float = 15.0,
) -> ValidatedManifest:
    """Validate the complete evaluation input before any API request is made."""
    if not math.isfinite(max_audio_minutes) or max_audio_minutes <= 0:
        raise ManifestError("max_audio_minutes는 유한한 0보다 큰 수여야 합니다.")

    path = Path(manifest_path).resolve()
    if not path.is_file():
        raise ManifestError(f"manifest를 찾을 수 없습니다: {manifest_path}")
    try:
        manifest_text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ManifestError("manifest를 읽을 수 없습니다.") from exc
    except UnicodeError as exc:
        raise ManifestError("manifest가 유효한 UTF-8이 아닙니다.") from exc

    samples: list[ManifestSample] = []
    seen_ids: set[str] = set()
    with StringIO(manifest_text, newline="") as source:
        reader = csv.DictReader(source)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ManifestError(f"manifest 필수 열이 없습니다: {', '.join(sorted(missing))}")

        for row_number, row in enumerate(reader, start=2):
            sample_id = (row.get("sample_id") or "").strip()
            if not sample_id:
                raise ManifestError(f"{row_number}행의 sample_id가 비었습니다.")
            if sample_id in {".", ".."} or any(
                character not in SAFE_SAMPLE_ID_CHARACTERS for character in sample_id
            ):
                raise ManifestError(
                    "sample_id에는 영문, 숫자, '.', '_', '-'만 사용할 수 있고 "
                    f"'.' 또는 '..'일 수 없습니다: {sample_id}"
                )
            if sample_id in seen_ids:
                raise ManifestError(f"중복 sample_id입니다: {sample_id}")
            seen_ids.add(sample_id)

            session_id = (row.get("session_id") or "").strip()
            stratum = (row.get("stratum") or "").strip()
            if not session_id:
                raise ManifestError(f"{row_number}행의 session_id가 비었습니다.")
            if not stratum:
                raise ManifestError(f"{row_number}행의 stratum이 비었습니다.")

            audio_value = row.get("audio_path") or ""
            reference_value = row.get("reference_path") or ""
            audio_path = _resolve_manifest_path(path.parent, audio_value, "audio_path", row_number)
            reference_path = _resolve_manifest_path(
                path.parent, reference_value, "reference_path", row_number
            )
            reference = _read_reference(reference_path, row_number, reference_value)
            duration_seconds = wav_duration_seconds(audio_path)

            samples.append(
                ManifestSample(
                    sample_id=sample_id,
                    audio_path=audio_path,
                    reference_path=reference_path,
                    session_id=session_id,
                    stratum=stratum,
                    duration_seconds=duration_seconds,
                    audio_sha256=file_sha256(audio_path),
                    reference_sha256=file_sha256(reference_path),
                    reference_text=reference,
                )
            )

    if not samples:
        raise ManifestError("manifest에 평가 표본이 없습니다.")

    total_duration = sum(sample.duration_seconds for sample in samples)
    limit_seconds = max_audio_minutes * 60
    if total_duration > limit_seconds + 1e-9:
        raise ManifestError(
            f"오디오 총 길이 {total_duration:.2f}초가 제한 "
            f"{limit_seconds:.2f}초를 초과합니다. API는 호출되지 않았습니다."
        )
    return ValidatedManifest(
        path=path,
        sha256=file_sha256(path),
        samples=tuple(samples),
        total_duration_seconds=total_duration,
    )


def _aggregate_strata(sample_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for result in sample_results:
        aggregate = totals.setdefault(
            result["stratum"],
            {
                "sample_count": 0,
                "total_audio_seconds": 0.0,
                "hits": 0,
                "substitutions": 0,
                "deletions": 0,
                "insertions": 0,
                "reference_characters": 0,
            },
        )
        aggregate["sample_count"] += 1
        aggregate["total_audio_seconds"] += result["duration_seconds"]
        for key in (
            "hits",
            "substitutions",
            "deletions",
            "insertions",
            "reference_characters",
        ):
            aggregate[key] += result[key]

    for aggregate in totals.values():
        errors = aggregate["substitutions"] + aggregate["deletions"] + aggregate["insertions"]
        aggregate["cer"] = errors / aggregate["reference_characters"]
    return totals


def evaluate_manifest(
    client: RTZRClient,
    manifest: ValidatedManifest,
    output_dir: str | Path,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Transcribe every validated sample and write per-sample and corpus metrics."""
    validate_wait_options(poll_interval, timeout)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    config_hash = config_sha256(DEFAULT_TRANSCRIBE_CONFIG)
    references: list[str] = []
    hypotheses: list[str] = []
    sample_results: list[dict[str, Any]] = []

    for index, sample in enumerate(manifest.samples, start=1):
        if progress is not None:
            progress(index, len(manifest.samples), sample.sample_id)

        response = client.transcribe(
            sample.audio_path,
            config=DEFAULT_TRANSCRIBE_CONFIG,
            poll_interval=poll_interval,
            timeout=timeout,
        )
        text_output = transcript_text(response)
        srt_output = transcript_srt(response)
        hypothesis = hypothesis_text(response)
        metrics = character_error_metrics(sample.reference_text, hypothesis)

        sample_dir = destination / sample.sample_id
        write_json_atomic(sample_dir / "response.json", response)
        write_text_atomic(sample_dir / "transcript.txt", text_output)
        write_text_atomic(sample_dir / "transcript.srt", srt_output)
        write_json_atomic(sample_dir / "metrics.json", metrics)

        references.append(sample.reference_text)
        hypotheses.append(hypothesis)
        sample_results.append(
            {
                "sample_id": sample.sample_id,
                "session_id": sample.session_id,
                "stratum": sample.stratum,
                "duration_seconds": sample.duration_seconds,
                "audio_sha256": sample.audio_sha256,
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
        "sample_count": len(manifest.samples),
        "total_audio_seconds": manifest.total_duration_seconds,
        "manifest_sha256": manifest.sha256,
        "config_sha256": config_hash,
        "corpus": corpus_character_error_metrics(references, hypotheses),
        "strata": _aggregate_strata(sample_results),
        "samples": sample_results,
    }
    write_json_atomic(destination / "summary.json", summary)
    return summary
