from __future__ import annotations

import csv
import json
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rtzr_stt.api import RTZRClient
from rtzr_stt.config import DEFAULT_TRANSCRIBE_CONFIG, config_sha256
from rtzr_stt.formatters import hypothesis_text, transcript_srt, transcript_text
from rtzr_stt.io import file_sha256, write_json_atomic, write_text_atomic
from rtzr_stt.metrics import (
    EmptyNormalizedText,
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


@dataclass(frozen=True)
class ValidatedManifest:
    path: Path
    samples: tuple[ManifestSample, ...]
    total_duration_seconds: float


def wav_duration_seconds(path: str | Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            frame_rate = audio.getframerate()
            if frame_rate <= 0:
                raise ManifestError(f"WAV sample rate가 올바르지 않습니다: {path}")
            return audio.getnframes() / frame_rate
    except (OSError, wave.Error) as exc:
        raise ManifestError(f"WAV 파일을 읽을 수 없습니다: {path}") from exc


def _resolve_manifest_path(base: Path, value: str, field: str, row_number: int) -> Path:
    if not value.strip():
        raise ManifestError(f"{row_number}행의 {field}가 비었습니다.")
    path = (base / value).resolve()
    if not path.is_file():
        raise ManifestError(f"{row_number}행의 {field} 파일이 없습니다: {value}")
    return path


def load_manifest(
    manifest_path: str | Path,
    *,
    max_audio_minutes: float = 15.0,
) -> ValidatedManifest:
    if max_audio_minutes <= 0:
        raise ManifestError("max_audio_minutes는 0보다 커야 합니다.")
    path = Path(manifest_path).resolve()
    if not path.is_file():
        raise ManifestError(f"manifest를 찾을 수 없습니다: {manifest_path}")

    samples: list[ManifestSample] = []
    seen_ids: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fieldnames = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - fieldnames
        if missing:
            raise ManifestError(f"manifest 필수 열이 없습니다: {', '.join(sorted(missing))}")
        for row_number, row in enumerate(reader, start=2):
            sample_id = (row.get("sample_id") or "").strip()
            if not sample_id:
                raise ManifestError(f"{row_number}행의 sample_id가 비었습니다.")
            if sample_id in seen_ids:
                raise ManifestError(f"중복 sample_id입니다: {sample_id}")
            if any(
                character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                for character in sample_id
            ):
                raise ManifestError(
                    f"sample_id에는 영문, 숫자, '.', '_', '-'만 사용할 수 있습니다: {sample_id}"
                )
            seen_ids.add(sample_id)

            audio_path = _resolve_manifest_path(
                path.parent, row.get("audio_path") or "", "audio_path", row_number
            )
            reference_path = _resolve_manifest_path(
                path.parent,
                row.get("reference_path") or "",
                "reference_path",
                row_number,
            )
            reference = reference_path.read_text(encoding="utf-8")
            try:
                if not normalize_for_spelling_cer(reference):
                    raise EmptyNormalizedText
            except (UnicodeError, EmptyNormalizedText) as exc:
                raise ManifestError(
                    f"{row_number}행의 정답이 정규화 후 비었습니다: {row.get('reference_path')}"
                ) from exc

            samples.append(
                ManifestSample(
                    sample_id=sample_id,
                    audio_path=audio_path,
                    reference_path=reference_path,
                    session_id=(row.get("session_id") or "").strip(),
                    stratum=(row.get("stratum") or "").strip(),
                    duration_seconds=wav_duration_seconds(audio_path),
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
    return ValidatedManifest(path, tuple(samples), total_duration)


def _load_cached_response(
    sample_dir: Path,
    *,
    audio_hash: str,
    config_hash: str,
) -> dict[str, Any] | None:
    cache_path = sample_dir / "cache.json"
    response_path = sample_dir / "response.json"
    if not cache_path.is_file() or not response_path.is_file():
        return None
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        response = json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(cache, dict)
        or cache.get("audio_sha256") != audio_hash
        or cache.get("config_sha256") != config_hash
        or not isinstance(response, dict)
        or response.get("status") != "completed"
        or not isinstance(response.get("results"), dict)
        or not isinstance(response["results"].get("utterances"), list)
    ):
        return None
    return response


def evaluate_manifest(
    client: RTZRClient,
    manifest: ValidatedManifest,
    output_dir: str | Path,
    *,
    resume: bool = False,
    poll_interval: float = 5.0,
    timeout: float = 1800.0,
) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    current_config_hash = config_sha256(DEFAULT_TRANSCRIBE_CONFIG)
    references: list[str] = []
    hypotheses: list[str] = []
    sample_results: list[dict[str, Any]] = []

    for sample in manifest.samples:
        sample_dir = destination / sample.sample_id
        sample_dir.mkdir(parents=True, exist_ok=True)
        audio_hash = file_sha256(sample.audio_path)
        response = (
            _load_cached_response(
                sample_dir,
                audio_hash=audio_hash,
                config_hash=current_config_hash,
            )
            if resume
            else None
        )
        cache_hit = response is not None
        if response is None:
            response = client.transcribe(
                sample.audio_path,
                config=DEFAULT_TRANSCRIBE_CONFIG,
                poll_interval=poll_interval,
                timeout=timeout,
            )
            write_json_atomic(sample_dir / "response.json", response)
            write_json_atomic(
                sample_dir / "cache.json",
                {
                    "audio_sha256": audio_hash,
                    "config_sha256": current_config_hash,
                },
            )

        text_output = transcript_text(response)
        hypothesis = hypothesis_text(response)
        reference = sample.reference_path.read_text(encoding="utf-8")
        metrics = character_error_metrics(reference, hypothesis)
        write_text_atomic(sample_dir / "transcript.txt", text_output)
        write_text_atomic(sample_dir / "transcript.srt", transcript_srt(response))
        write_json_atomic(sample_dir / "metrics.json", metrics)

        references.append(reference)
        hypotheses.append(hypothesis)
        sample_results.append(
            {
                "sample_id": sample.sample_id,
                "session_id": sample.session_id,
                "stratum": sample.stratum,
                "duration_seconds": sample.duration_seconds,
                "cache_hit": cache_hit,
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

    corpus = corpus_character_error_metrics(references, hypotheses)
    summary: dict[str, Any] = {
        "sample_count": len(manifest.samples),
        "total_audio_seconds": manifest.total_duration_seconds,
        "config_sha256": current_config_hash,
        "corpus": corpus,
        "samples": sample_results,
    }
    write_json_atomic(destination / "summary.json", summary)
    return summary
