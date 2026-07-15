from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Any

from jiwer import process_characters

DUAL_TRANSCRIPTION = re.compile(r"\(([^()]*)\)/\(([^()]*)\)")
NOISE_TAG = re.compile(r"(?<![A-Za-z])[blon]/", flags=re.IGNORECASE)
ANNOTATION_MARKS = str.maketrans("", "", "/*+()")


class EmptyNormalizedText(ValueError):
    pass


def normalize_for_spelling_cer(text: str) -> str:
    """Normalize both references and hypotheses for spelling-form Korean CER."""
    normalized = unicodedata.normalize("NFC", text)
    previous = None
    while previous != normalized:
        previous = normalized
        normalized = DUAL_TRANSCRIPTION.sub(lambda match: match.group(1), normalized)
    normalized = NOISE_TAG.sub("", normalized)
    normalized = normalized.translate(ANNOTATION_MARKS)
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character)[0] not in {"P", "S"}
    )
    normalized = normalized.lower()
    return "".join(normalized.split())


def _validated_normalized(text: str, role: str) -> str:
    normalized = normalize_for_spelling_cer(text)
    if not normalized:
        raise EmptyNormalizedText(f"정규화 후 {role} 문자열이 비었습니다.")
    return normalized


def character_error_metrics(reference: str, hypothesis: str) -> dict[str, Any]:
    ref = _validated_normalized(reference, "정답")
    hyp = normalize_for_spelling_cer(hypothesis)
    output = process_characters(ref, hyp)
    reference_characters = output.hits + output.substitutions + output.deletions
    return {
        "cer": output.cer,
        "hits": output.hits,
        "substitutions": output.substitutions,
        "deletions": output.deletions,
        "insertions": output.insertions,
        "reference_characters": reference_characters,
        "normalized_reference": ref,
        "normalized_hypothesis": hyp,
    }


def corpus_character_error_metrics(
    references: Sequence[str], hypotheses: Sequence[str]
) -> dict[str, Any]:
    if len(references) != len(hypotheses):
        raise ValueError("정답과 가설의 개수가 다릅니다.")
    if not references:
        raise ValueError("CER을 계산할 표본이 없습니다.")
    normalized_references = [_validated_normalized(reference, "정답") for reference in references]
    normalized_hypotheses = [normalize_for_spelling_cer(hypothesis) for hypothesis in hypotheses]
    output = process_characters(normalized_references, normalized_hypotheses)
    reference_characters = output.hits + output.substitutions + output.deletions
    return {
        "cer": output.cer,
        "hits": output.hits,
        "substitutions": output.substitutions,
        "deletions": output.deletions,
        "insertions": output.insertions,
        "reference_characters": reference_characters,
    }
