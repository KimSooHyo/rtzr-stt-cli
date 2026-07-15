from __future__ import annotations

from typing import Any


def _utterance_message(utterance: object, index: int) -> str | None:
    if not isinstance(utterance, dict):
        raise ValueError(f"results.utterances[{index}]가 객체가 아닙니다.")
    message = utterance.get("msg")
    if not isinstance(message, str):
        raise ValueError(f"results.utterances[{index}].msg가 문자열이 아닙니다.")
    stripped = message.strip()
    return stripped or None


def nonempty_utterances(response: dict[str, Any]) -> list[dict[str, Any]]:
    results = response.get("results")
    utterances = results.get("utterances") if isinstance(results, dict) else None
    if not isinstance(utterances, list):
        raise ValueError("응답에 results.utterances 배열이 없습니다.")
    nonempty: list[dict[str, Any]] = []
    for index, utterance in enumerate(utterances):
        message = _utterance_message(utterance, index)
        if message is not None:
            nonempty.append(utterance)
    return nonempty


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
        if type(start) is not int or type(duration) is not int:
            raise ValueError("SRT 생성에 필요한 start_at과 duration은 정수여야 합니다.")
        if start < 0 or duration < 0:
            raise ValueError("SRT 생성에 필요한 start_at과 duration은 0 이상이어야 합니다.")
        end = start + duration
        blocks.append(
            f"{index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n"
            f"{utterance['msg'].strip()}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def hypothesis_text(response: dict[str, Any]) -> str:
    return " ".join(utterance["msg"].strip() for utterance in nonempty_utterances(response))
