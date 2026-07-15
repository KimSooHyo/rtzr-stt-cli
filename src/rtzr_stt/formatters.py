from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def nonempty_utterances(response: dict[str, Any]) -> list[dict[str, Any]]:
    results = response.get("results")
    utterances = results.get("utterances") if isinstance(results, dict) else None
    if not isinstance(utterances, list):
        raise ValueError("응답에 results.utterances 배열이 없습니다.")
    return [
        utterance
        for utterance in utterances
        if isinstance(utterance, dict)
        and isinstance(utterance.get("msg"), str)
        and utterance["msg"].strip()
    ]


def transcript_text(response: dict[str, Any]) -> str:
    messages = [utterance["msg"].strip() for utterance in nonempty_utterances(response)]
    return "\n".join(messages) + ("\n" if messages else "")


def _srt_timestamp(milliseconds: int) -> str:
    if milliseconds < 0:
        raise ValueError("SRT timestamp는 음수일 수 없습니다.")
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def transcript_srt(response: dict[str, Any]) -> str:
    blocks: list[str] = []
    for index, utterance in enumerate(nonempty_utterances(response), start=1):
        start = utterance.get("start_at")
        duration = utterance.get("duration")
        if not isinstance(start, int) or not isinstance(duration, int):
            raise ValueError("SRT 생성에 필요한 start_at 또는 duration이 없습니다.")
        end = start + duration
        blocks.append(
            f"{index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n"
            f"{utterance['msg'].strip()}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def hypothesis_text(response: dict[str, Any]) -> str:
    return " ".join(utterance["msg"].strip() for utterance in nonempty_utterances(response))


def utterance_messages(utterances: Iterable[dict[str, Any]]) -> str:
    return " ".join(
        str(item.get("msg", "")).strip() for item in utterances if str(item.get("msg", "")).strip()
    )
