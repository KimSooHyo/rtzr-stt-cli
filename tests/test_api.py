from __future__ import annotations

from urllib.parse import parse_qs

import pytest
import requests
import responses

from rtzr_stt.api import (
    APIRequestError,
    APIResponseError,
    RTZRClient,
    TranscriptionFailed,
    TranscriptionTimeout,
)
from rtzr_stt.config import BASE_URL, DEFAULT_TRANSCRIBE_CONFIG, canonical_config_json

AUTH_URL = f"{BASE_URL}/v1/authenticate"
TRANSCRIBE_URL = f"{BASE_URL}/v1/transcribe"


def completed_response() -> dict:
    return {
        "id": "job-1",
        "status": "completed",
        "results": {
            "utterances": [
                {
                    "start_at": 0,
                    "duration": 1000,
                    "msg": "안녕하세요.",
                    "spk": 0,
                    "lang": "ko",
                }
            ]
        },
    }


@responses.activate
def test_auth_multipart_and_polling_contract(tmp_path):
    assert DEFAULT_TRANSCRIBE_CONFIG == {
        "model_name": "sommers",
        "language": "ko",
        "domain": "GENERAL",
        "use_diarization": False,
        "use_itn": True,
        "use_disfluency_filter": False,
        "use_profanity_filter": False,
        "use_paragraph_splitter": False,
        "use_word_timestamp": False,
        "keywords": [],
    }
    audio = tmp_path / "private-name.wav"
    audio.write_bytes(b"RIFF-test-audio")
    observed: dict[str, object] = {}

    def auth_callback(request):
        observed["auth_content_type"] = request.headers["Content-Type"]
        observed["auth_body"] = parse_qs(request.body)
        return 200, {"Content-Type": "application/json"}, '{"access_token":"token-1"}'

    def submit_callback(request):
        body = request.body if isinstance(request.body, bytes) else request.body.encode()
        observed["authorization"] = request.headers["Authorization"]
        observed["multipart_content_type"] = request.headers["Content-Type"]
        observed["multipart_body"] = body
        return 200, {"Content-Type": "application/json"}, '{"id":"job-1"}'

    responses.add_callback(responses.POST, AUTH_URL, callback=auth_callback)
    responses.add_callback(responses.POST, TRANSCRIBE_URL, callback=submit_callback)
    responses.add(
        responses.GET,
        f"{TRANSCRIBE_URL}/job-1",
        json={"id": "job-1", "status": "transcribing"},
    )
    responses.add(responses.GET, f"{TRANSCRIBE_URL}/job-1", json=completed_response())

    sleep_calls: list[float] = []
    result = RTZRClient("client-id", "client-secret", sleep=sleep_calls.append).transcribe(
        audio,
        poll_interval=5,
    )

    assert result["status"] == "completed"
    assert observed["auth_content_type"].startswith("application/x-www-form-urlencoded")
    assert observed["auth_body"] == {
        "client_id": ["client-id"],
        "client_secret": ["client-secret"],
    }
    assert observed["authorization"] == "Bearer token-1"
    assert observed["multipart_content_type"].startswith("multipart/form-data; boundary=")
    multipart = observed["multipart_body"]
    assert b'name="file"; filename="audio.wav"' in multipart
    assert b'name="config"' in multipart
    assert canonical_config_json(DEFAULT_TRANSCRIBE_CONFIG).encode() in multipart
    assert sleep_calls == [5]


def test_auth_and_get_use_independent_request_timeout():
    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class RecordingSession:
        def __init__(self):
            self.timeouts: list[float] = []

        def post(self, *args, timeout, **kwargs):
            self.timeouts.append(timeout)
            return FakeResponse({"access_token": "token"})

        def request(self, *args, timeout, **kwargs):
            self.timeouts.append(timeout)
            return FakeResponse(completed_response())

    session = RecordingSession()
    client = RTZRClient("id", "secret", session=session, request_timeout=12.5)

    assert client.get("job-1")["status"] == "completed"
    assert session.timeouts == [12.5, 12.5]


@responses.activate
def test_get_retries_transient_statuses_with_bounded_backoff():
    for status in [429, 500, 502, 503, 504]:
        responses.reset()
        responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
        for _ in range(3):
            responses.add(
                responses.GET,
                f"{TRANSCRIBE_URL}/job-1",
                status=status,
                json={"code": "TEMPORARY"},
            )
        responses.add(responses.GET, f"{TRANSCRIBE_URL}/job-1", json=completed_response())
        sleep_calls: list[float] = []

        result = RTZRClient("id", "secret", sleep=sleep_calls.append).get("job-1")

        assert result["status"] == "completed"
        assert sleep_calls == [1.0, 2.0, 4.0]

    responses.reset()
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
    for _ in range(4):
        responses.add(
            responses.GET,
            f"{TRANSCRIBE_URL}/job-1",
            status=503,
            json={"code": "TEMPORARY"},
        )
    sleep_calls = []

    with pytest.raises(APIRequestError) as captured:
        RTZRClient("id", "secret", sleep=sleep_calls.append).get("job-1")

    assert captured.value.status == 503
    assert sleep_calls == [1.0, 2.0, 4.0]
    assert sum(call.request.method == "GET" for call in responses.calls) == 4


@responses.activate
def test_get_retries_network_errors_with_bounded_backoff():
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
    for _ in range(3):
        responses.add(
            responses.GET,
            f"{TRANSCRIBE_URL}/job-1",
            body=requests.ConnectionError("temporary disconnect"),
        )
    responses.add(responses.GET, f"{TRANSCRIBE_URL}/job-1", json=completed_response())
    sleep_calls: list[float] = []

    result = RTZRClient("id", "secret", sleep=sleep_calls.append).get("job-1")

    assert result["status"] == "completed"
    assert sleep_calls == [1.0, 2.0, 4.0]


@responses.activate
def test_ambiguous_submit_failure_is_not_retried(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    failures = [
        requests.ConnectionError("connection dropped after upload"),
        (503, {"code": "E503"}),
    ]
    for failure in failures:
        responses.reset()
        responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
        if isinstance(failure, Exception):
            responses.add(responses.POST, TRANSCRIBE_URL, body=failure)
        else:
            status, payload = failure
            responses.add(responses.POST, TRANSCRIBE_URL, status=status, json=payload)

        with pytest.raises(APIRequestError):
            RTZRClient("id", "secret").submit(audio)

        assert sum(call.request.url == TRANSCRIBE_URL for call in responses.calls) == 1


@responses.activate
def test_submit_retries_a0002_and_rewinds_file(tmp_path):
    audio = tmp_path / "private-name.wav"
    audio.write_bytes(b"unique-audio-content")
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
    responses.add(
        responses.POST,
        TRANSCRIBE_URL,
        status=429,
        json={"code": "A0002", "msg": "too many concurrent requests"},
    )

    def submit_callback(request):
        body = request.body if isinstance(request.body, bytes) else request.body.encode()
        assert b"unique-audio-content" in body
        assert b'filename="audio.wav"' in body
        return 200, {"Content-Type": "application/json"}, '{"id":"job-1"}'

    responses.add_callback(responses.POST, TRANSCRIBE_URL, callback=submit_callback)
    sleep_calls: list[float] = []

    result = RTZRClient("id", "secret", sleep=sleep_calls.append).submit(audio)

    assert result == "job-1"
    assert sleep_calls == [1.0]

    responses.reset()
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
    for _ in range(4):
        responses.add(
            responses.POST,
            TRANSCRIBE_URL,
            status=429,
            json={"code": "A0002"},
        )
    sleep_calls = []

    with pytest.raises(APIRequestError) as captured:
        RTZRClient("id", "secret", sleep=sleep_calls.append).submit(audio)

    assert captured.value.code == "A0002"
    assert sleep_calls == [1.0, 2.0, 4.0]
    assert sum(call.request.url == TRANSCRIBE_URL for call in responses.calls) == 4


@responses.activate
def test_submit_does_not_retry_other_429_code(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
    responses.add(responses.POST, TRANSCRIBE_URL, status=429, json={"code": "A0001"})

    with pytest.raises(APIRequestError) as captured:
        RTZRClient("id", "secret").submit(audio)

    assert captured.value.code == "A0001"
    assert sum(call.request.url == TRANSCRIBE_URL for call in responses.calls) == 1


@responses.activate
def test_submit_401_reauthenticates_and_rewinds_file(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"unique-audio-content")
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token-1"})
    responses.add(responses.POST, TRANSCRIBE_URL, status=401, json={"code": "H0002"})
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token-2"})

    def submit_callback(request):
        body = request.body if isinstance(request.body, bytes) else request.body.encode()
        assert b"unique-audio-content" in body
        assert request.headers["Authorization"] == "Bearer token-2"
        return 200, {"Content-Type": "application/json"}, '{"id":"job-1"}'

    responses.add_callback(responses.POST, TRANSCRIBE_URL, callback=submit_callback)

    assert RTZRClient("id", "secret").submit(audio) == "job-1"


@responses.activate
def test_401_reauthentication_happens_at_most_once():
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token-1"})
    responses.add(responses.GET, f"{TRANSCRIBE_URL}/job-1", status=401, json={"code": "H0002"})
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token-2"})
    responses.add(responses.GET, f"{TRANSCRIBE_URL}/job-1", status=401, json={"code": "H0002"})

    with pytest.raises(APIRequestError) as captured:
        RTZRClient("id", "secret").get("job-1")

    assert captured.value.status == 401
    assert sum(call.request.url == AUTH_URL for call in responses.calls) == 2


@responses.activate
def test_wait_rejects_failed_or_unknown_status():
    cases = [
        (
            {"id": "job-1", "status": "failed", "error": {"code": "E500"}},
            TranscriptionFailed,
            "E500",
        ),
        ({"id": "job-1", "status": "queued"}, APIResponseError, "알 수 없는"),
    ]
    for payload, exception, message in cases:
        responses.reset()
        responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
        responses.add(responses.GET, f"{TRANSCRIBE_URL}/job-1", json=payload)

        with pytest.raises(exception, match=message):
            RTZRClient("id", "secret").wait_for_completion("job-1")


@responses.activate
def test_polling_timeout_uses_elapsed_time():
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
    for _ in range(2):
        responses.add(
            responses.GET,
            f"{TRANSCRIBE_URL}/job-1",
            json={"id": "job-1", "status": "transcribing"},
        )
    client = RTZRClient("id", "secret", sleep=sleep, monotonic=lambda: now[0])

    with pytest.raises(TranscriptionTimeout):
        client.wait_for_completion("job-1", poll_interval=5, timeout=6)


def test_transcribe_validates_wait_options_before_submit(tmp_path, monkeypatch):
    invalid_options = [
        (float("nan"), 10.0),
        (float("inf"), 10.0),
        (1.0, 0.0),
        (1.0, -1.0),
    ]
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    client = RTZRClient("id", "secret")

    def unexpected_submit(*args, **kwargs):
        raise AssertionError("invalid options must fail before submit")

    monkeypatch.setattr(client, "submit", unexpected_submit)

    for poll_interval, timeout in invalid_options:
        with pytest.raises(ValueError):
            client.transcribe(audio, poll_interval=poll_interval, timeout=timeout)


def test_request_timeout_must_be_finite_and_positive():
    for request_timeout in [0.0, -1.0, float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="request_timeout"):
            RTZRClient("id", "secret", request_timeout=request_timeout)


@responses.activate
def test_required_response_fields(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    responses.add(responses.POST, AUTH_URL, json={})
    with pytest.raises(APIResponseError, match="access_token"):
        RTZRClient("id", "secret").authenticate()

    responses.reset()
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
    responses.add(responses.POST, TRANSCRIBE_URL, json={})
    with pytest.raises(APIResponseError, match="id"):
        RTZRClient("id", "secret").submit(audio)


@responses.activate
def test_completed_response_requires_utterances():
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
    responses.add(
        responses.GET,
        f"{TRANSCRIBE_URL}/job-1",
        json={"id": "job-1", "status": "completed", "results": {}},
    )

    with pytest.raises(APIResponseError, match="utterances"):
        RTZRClient("id", "secret").wait_for_completion("job-1")


@responses.activate
def test_credentials_and_server_message_are_not_exposed_in_error():
    client_id = "PRIVATE-CLIENT-ID"
    client_secret = "PRIVATE-CLIENT-SECRET"
    responses.add(
        responses.POST,
        AUTH_URL,
        status=401,
        json={"code": "H0002", "msg": f"{client_id} {client_secret} invalid"},
    )

    with pytest.raises(APIRequestError) as captured:
        RTZRClient(client_id, client_secret).authenticate()

    message = str(captured.value)
    assert client_id not in message
    assert client_secret not in message
    assert "invalid" not in message
