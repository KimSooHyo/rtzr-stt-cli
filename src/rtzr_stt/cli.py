from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rtzr_stt.api import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_WAIT_TIMEOUT_SECONDS,
    RTZRClient,
    RTZRError,
    validate_wait_options,
)
from rtzr_stt.config import CredentialError, DEFAULT_TRANSCRIBE_CONFIG, load_credentials
from rtzr_stt.evaluation import (
    DEFAULT_SAMPLE_COUNT,
    EvaluationDataError,
    evaluate_fleurs,
    prepare_fleurs_evaluation,
)
from rtzr_stt.formatters import hypothesis_text, transcript_srt, transcript_text
from rtzr_stt.io import (
    validate_empty_output_directory,
    write_json_atomic,
    write_text_atomic,
)
from rtzr_stt.metrics import (
    EmptyReferenceText,
    character_error_metrics,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtzr-stt",
        description="FLEURS 한국어 또는 사용자 오디오를 RTZR Batch STT API로 전사합니다.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    transcribe = subparsers.add_parser(
        "transcribe",
        help="오디오 한 건 전사",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    transcribe.add_argument("audio", type=Path, help="전사할 오디오 파일")
    transcribe.add_argument(
        "--format",
        choices=("txt", "srt", "all"),
        default="all",
        help="생성할 전사 형식",
    )
    transcribe.add_argument("--output-dir", type=Path, required=True, help="결과 디렉터리")
    transcribe.add_argument("--reference", type=Path, help="CER 계산용 UTF-8 정답")
    transcribe.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="결과 조회 간격(초)",
    )
    transcribe.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_WAIT_TIMEOUT_SECONDS,
        help="전사 결과 대기 제한(초)",
    )

    evaluate = subparsers.add_parser(
        "evaluate",
        help="FLEURS 한국어 STT 실행 및 CER 평가",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    evaluate.add_argument(
        "--output-dir", type=Path, required=True, help="FLEURS 전사·평가 결과 디렉터리"
    )
    evaluate.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLE_COUNT,
        help="FLEURS validation 앞에서부터 실행할 표본 수(1은 smoke test)",
    )
    evaluate.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="결과 조회 간격(초)",
    )
    evaluate.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_WAIT_TIMEOUT_SECONDS,
        help="파일별 전사 결과 대기 제한(초)",
    )
    return parser


def _build_client() -> RTZRClient:
    client_id, client_secret = load_credentials()
    return RTZRClient(client_id, client_secret)


def _read_reference(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"정답 파일을 찾을 수 없습니다: {path}")
    reference = path.read_text(encoding="utf-8").strip()
    if not reference:
        raise EmptyReferenceText("정답 문자열이 비었습니다.")
    return reference


def _run_transcribe(args: argparse.Namespace) -> int:
    validate_wait_options(args.poll_interval, args.timeout)
    if not args.audio.is_file():
        raise FileNotFoundError(f"오디오 파일을 찾을 수 없습니다: {args.audio}")
    reference = _read_reference(args.reference) if args.reference is not None else None
    validate_empty_output_directory(args.output_dir)

    client = _build_client()
    response = client.transcribe(
        args.audio,
        config=DEFAULT_TRANSCRIBE_CONFIG,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )

    text_output = transcript_text(response) if args.format in {"txt", "all"} else None
    srt_output = transcript_srt(response) if args.format in {"srt", "all"} else None
    metrics = (
        character_error_metrics(reference, hypothesis_text(response))
        if reference is not None
        else None
    )

    write_json_atomic(args.output_dir / "response.json", response)
    if text_output is not None:
        write_text_atomic(args.output_dir / "transcript.txt", text_output)
    if srt_output is not None:
        write_text_atomic(args.output_dir / "transcript.srt", srt_output)
    if metrics is not None:
        write_json_atomic(args.output_dir / "metrics.json", metrics)
        print(f"CER: {metrics['cer'] * 100:.2f}%")

    print(f"결과 저장: {args.output_dir}")
    return 0


def _run_evaluate(args: argparse.Namespace) -> int:
    validate_wait_options(args.poll_interval, args.timeout)
    validate_empty_output_directory(args.output_dir)
    with prepare_fleurs_evaluation(args.samples) as prepared:
        client = _build_client()
        summary = evaluate_fleurs(
            client,
            prepared,
            args.output_dir,
            poll_interval=args.poll_interval,
            timeout=args.timeout,
            progress=lambda current, total, sample_id: print(
                f"[{current}/{total}] {sample_id}",
                file=sys.stderr,
            ),
        )
    print(f"표본: {summary['sample_count']}개")
    print(f"총 오디오: {summary['total_audio_seconds']:.2f}초")
    print(f"Corpus CER: {summary['corpus']['cer'] * 100:.2f}%")
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
        EmptyReferenceText,
        EvaluationDataError,
        OSError,
        RTZRError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    return 2
