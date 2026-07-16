from __future__ import annotations

import pytest

from rtzr_stt.formatters import transcript_srt, transcript_text
from rtzr_stt.metrics import (
    EmptyReferenceText,
    character_error_metrics,
    corpus_character_error_metrics,
)


@pytest.mark.parametrize(
    ("reference", "hypothesis", "error_type", "reference_characters", "cer"),
    [
        ("가 나", "가나", "deletions", 3, 1 / 3),
        ("가!", "가", "deletions", 2, 1 / 2),
        ("A", "a", "substitutions", 1, 1.0),
        (" 가", "가", "deletions", 2, 1 / 2),
        ("가", " 가", "insertions", 1, 1.0),
        ("가", "가", "deletions", 2, 1.0),
    ],
)
def test_exact_cer_counts_all_characters(
    reference, hypothesis, error_type, reference_characters, cer
):
    metrics = character_error_metrics(reference, hypothesis)

    assert metrics[error_type] == 1
    assert metrics["reference_characters"] == reference_characters
    assert metrics["cer"] == pytest.approx(cer)
    assert metrics["reference"] == reference
    assert metrics["hypothesis"] == hypothesis


def test_empty_reference_is_rejected():
    with pytest.raises(EmptyReferenceText):
        character_error_metrics("", "가설")


def test_single_and_corpus_micro_cer():
    single = character_error_metrics("abc", "axc")
    assert single["substitutions"] == 1
    assert single["cer"] == pytest.approx(1 / 3)

    corpus = corpus_character_error_metrics(["abc", "de"], ["axc", "d"])
    assert corpus["substitutions"] == 1
    assert corpus["deletions"] == 1
    assert corpus["reference_characters"] == 5
    assert corpus["cer"] == pytest.approx(2 / 5)


def test_corpus_requires_matching_nonempty_input():
    with pytest.raises(ValueError, match="개수가 다릅니다"):
        corpus_character_error_metrics(["정답"], [])
    with pytest.raises(ValueError, match="표본이 없습니다"):
        corpus_character_error_metrics([], [])


def test_txt_and_srt_output_skip_empty_utterances():
    response = {
        "results": {
            "utterances": [
                {"start_at": 0, "duration": 1234, "msg": "첫 문장"},
                {"start_at": 1234, "duration": 100, "msg": "  "},
                {"start_at": 3723004, "duration": 2000, "msg": "둘째 문장"},
            ]
        }
    }
    assert transcript_text(response) == "첫 문장\n둘째 문장\n"
    assert transcript_srt(response) == (
        "1\n00:00:00,000 --> 00:00:01,234\n첫 문장\n\n2\n01:02:03,004 --> 01:02:05,004\n둘째 문장\n"
    )


@pytest.mark.parametrize(
    "utterance",
    [
        None,
        {},
        {"msg": 123, "start_at": 0, "duration": 100},
    ],
)
def test_formatter_rejects_malformed_utterances(utterance):
    response = {"results": {"utterances": [utterance]}}

    with pytest.raises(ValueError, match=r"utterances\[0\]"):
        transcript_text(response)


def test_srt_intentionally_skips_whitespace_only_message_without_timing():
    response = {"results": {"utterances": [{"msg": "  \n  "}]}}

    assert transcript_srt(response) == ""


@pytest.mark.parametrize(
    ("start", "duration"),
    [
        (True, 100),
        (0, False),
        (-1, 100),
        (0, -1),
    ],
)
def test_srt_requires_nonnegative_integer_timing(start, duration):
    response = {
        "results": {"utterances": [{"msg": "문장", "start_at": start, "duration": duration}]}
    }

    with pytest.raises(ValueError, match="start_at"):
        transcript_srt(response)


def test_srt_requires_timing_fields():
    response = {"results": {"utterances": [{"msg": "문장"}]}}
    with pytest.raises(ValueError, match="start_at"):
        transcript_srt(response)
