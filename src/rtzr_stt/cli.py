from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rtzr_stt.api import RTZRClient, RTZRError
from rtzr_stt.config import CredentialError, DEFAULT_TRANSCRIBE_CONFIG, load_credentials
from rtzr_stt.evaluation import ManifestError, evaluate_manifest, load_manifest
from rtzr_stt.formatters import hypothesis_text, transcript_srt, transcript_text
from rtzr_stt.io import write_json_atomic, write_text_atomic
from rtzr_stt.metrics import EmptyNormalizedText, character_error_metrics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtzr-stt",
        description="RTZR Batch STT API로 오디오를 TXT/SRT로 전사합니다.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="credential을 읽을 dotenv 파일 (기본값: .env)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    transcribe = subparsers.add_parser("transcribe", help="오디오 한 건 전사")
    transcribe.add_argument("audio", type=Path)
    transcribe.add_argument("--format", choices=("txt", "srt", "all"), default="all")
    transcribe.add_argument("--output-dir", type=Path, required=True)
    transcribe.add_argument("--reference", type=Path)
    transcribe.add_argument("--poll-interval", type=float, default=5.0)
    transcribe.add_argument("--timeout", type=float, default=1800.0)

    evaluate = subparsers.add_parser("evaluate", help="manifest 순차 평가")
    evaluate.add_argument("manifest", type=Path)
    evaluate.add_argument("--output-dir", type=Path, required=True)
    evaluate.add_argument("--max-audio-minutes", type=float, default=15.0)
    evaluate.add_argument("--resume", action="store_true")
    evaluate.add_argument("--poll-interval", type=float, default=5.0)
    evaluate.add_argument("--timeout", type=float, default=1800.0)
    return parser


def _build_client(env_file: str | Path) -> RTZRClient:
    client_id, client_secret = load_credentials(env_file)
    return RTZRClient(client_id, client_secret)


def _run_transcribe(args: argparse.Namespace) -> int:
    if not args.audio.is_file():
        raise FileNotFoundError(f"오디오 파일을 찾을 수 없습니다: {args.audio}")

    reference: str | None = None
    if args.reference is not None:
        if not args.reference.is_file():
            raise FileNotFoundError(f"정답 파일을 찾을 수 없습니다: {args.reference}")
        reference = args.reference.read_text(encoding="utf-8")
        # Validate before any network request.
        character_error_metrics(reference, "")

    client = _build_client(args.env_file)
    response = client.transcribe(
        args.audio,
        config=DEFAULT_TRANSCRIBE_CONFIG,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(args.output_dir / "response.json", response)
    if args.format in {"txt", "all"}:
        write_text_atomic(args.output_dir / "transcript.txt", transcript_text(response))
    if args.format in {"srt", "all"}:
        write_text_atomic(args.output_dir / "transcript.srt", transcript_srt(response))
    if reference is not None:
        metrics = character_error_metrics(reference, hypothesis_text(response))
        write_json_atomic(args.output_dir / "metrics.json", metrics)
        print(f"CER: {metrics['cer'] * 100:.2f}% (낮을수록 좋음)")
    print(f"결과 저장: {args.output_dir}")
    return 0


def _run_evaluate(args: argparse.Namespace) -> int:
    # Complete all local validation before creating an API client.
    manifest = load_manifest(args.manifest, max_audio_minutes=args.max_audio_minutes)
    client = _build_client(args.env_file)
    summary = evaluate_manifest(
        client,
        manifest,
        args.output_dir,
        resume=args.resume,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )
    print(f"표본: {summary['sample_count']}개")
    print(f"총 오디오: {summary['total_audio_seconds']:.2f}초")
    print(f"Corpus CER: {summary['corpus']['cer'] * 100:.2f}% (낮을수록 좋음)")
    print(f"결과 저장: {args.output_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "transcribe":
            return _run_transcribe(args)
        if args.command == "evaluate":
            return _run_evaluate(args)
        parser.error(f"지원하지 않는 명령입니다: {args.command}")
    except (
        CredentialError,
        EmptyNormalizedText,
        FileNotFoundError,
        ManifestError,
        RTZRError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    return 2
