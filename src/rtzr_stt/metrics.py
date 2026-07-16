from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from jiwer import Compose, ReduceToListOfListOfChars, process_characters

EXACT_CHARACTER_TRANSFORM = Compose([ReduceToListOfListOfChars()])


class EmptyReferenceText(ValueError):
    pass


def _validated_reference(text: str) -> str:
    if not text:
        raise EmptyReferenceText("정답 문자열이 비었습니다.")
    return text


def character_error_metrics(reference: str, hypothesis: str) -> dict[str, Any]:
    """Calculate exact CER without linguistic or formatting normalization."""
    ref = _validated_reference(reference)
    output = process_characters(
        ref,
        hypothesis,
        reference_transform=EXACT_CHARACTER_TRANSFORM,
        hypothesis_transform=EXACT_CHARACTER_TRANSFORM,
    )
    reference_characters = output.hits + output.substitutions + output.deletions
    return {
        "cer": output.cer,
        "hits": output.hits,
        "substitutions": output.substitutions,
        "deletions": output.deletions,
        "insertions": output.insertions,
        "reference_characters": reference_characters,
        "reference": ref,
        "hypothesis": hypothesis,
    }


def corpus_character_error_metrics(
    references: Sequence[str], hypotheses: Sequence[str]
) -> dict[str, Any]:
    if len(references) != len(hypotheses):
        raise ValueError("정답과 가설의 개수가 다릅니다.")
    if not references:
        raise ValueError("CER을 계산할 표본이 없습니다.")
    validated_references = [_validated_reference(reference) for reference in references]
    output = process_characters(
        validated_references,
        list(hypotheses),
        reference_transform=EXACT_CHARACTER_TRANSFORM,
        hypothesis_transform=EXACT_CHARACTER_TRANSFORM,
    )
    reference_characters = output.hits + output.substitutions + output.deletions
    return {
        "cer": output.cer,
        "hits": output.hits,
        "substitutions": output.substitutions,
        "deletions": output.deletions,
        "insertions": output.insertions,
        "reference_characters": reference_characters,
    }
