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


def completed_response(transcribe_id: str = "job-1") -> dict:
    return {
        "id": transcribe_id,
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
def test_auth_and_multipart_contract(tmp_path):
    audio = tmp_path / "sample.wav"
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
    client = RTZRClient("client-id", "client-secret", sleep=sleep_calls.append)
    result = client.transcribe(audio, poll_interval=5)

    assert result["status"] == "completed"
    assert observed["auth_content_type"].startswith("application/x-www-form-urlencoded")
    assert observed["auth_body"] == {
        "client_id": ["client-id"],
        "client_secret": ["client-secret"],
    }
    assert observed["authorization"] == "Bearer token-1"
    assert observed["multipart_content_type"].startswith("multipart/form-data; boundary=")
    multipart = observed["multipart_body"]
    assert b'name="file"; filename="sample.wav"' in multipart
    assert b'name="config"' in multipart
    assert canonical_config_json(DEFAULT_TRANSCRIBE_CONFIG).encode() in multipart
    assert sleep_calls == [5]


@responses.activate
def test_get_429_uses_exponential_backoff(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
    responses.add(responses.POST, TRANSCRIBE_URL, json={"id": "job-1"})
    responses.add(
        responses.GET,
        f"{TRANSCRIBE_URL}/job-1",
        status=429,
        json={"code": "A0003", "msg": "too many requests"},
    )
    responses.add(responses.GET, f"{TRANSCRIBE_URL}/job-1", json=completed_response())

    sleep_calls: list[float] = []
    client = RTZRClient("id", "secret", sleep=sleep_calls.append)
    assert client.transcribe(audio)["status"] == "completed"
    assert sleep_calls == [1.0]


@responses.activate
def test_ambiguous_submit_network_failure_is_not_retried(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
    responses.add(
        responses.POST,
        TRANSCRIBE_URL,
        body=requests.ConnectionError("connection dropped after upload"),
    )
    client = RTZRClient("id", "secret")
    with pytest.raises(APIRequestError, match="NETWORK_ERROR"):
        client.submit(audio)
    assert sum(call.request.url == TRANSCRIBE_URL for call in responses.calls) == 1


@responses.activate
def test_submit_401_rewinds_file_before_safe_reauthentication(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"unique-audio-content")
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token-1"})
    responses.add(
        responses.POST,
        TRANSCRIBE_URL,
        status=401,
        json={"code": "H0002"},
    )
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token-2"})

    def submit_callback(request):
        body = request.body if isinstance(request.body, bytes) else request.body.encode()
        assert b"unique-audio-content" in body
        assert request.headers["Authorization"] == "Bearer token-2"
        return 200, {"Content-Type": "application/json"}, '{"id":"job-1"}'

    responses.add_callback(responses.POST, TRANSCRIBE_URL, callback=submit_callback)
    client = RTZRClient("id", "secret")
    assert client.submit(audio) == "job-1"


@responses.activate
def test_401_reauthenticates_once_during_polling(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token-1"})
    responses.add(responses.POST, TRANSCRIBE_URL, json={"id": "job-1"})
    responses.add(
        responses.GET,
        f"{TRANSCRIBE_URL}/job-1",
        status=401,
        json={"code": "H0002"},
    )
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token-2"})

    def completed_callback(request):
        assert request.headers["Authorization"] == "Bearer token-2"
        return (
            200,
            {"Content-Type": "application/json"},
            '{"id":"job-1","status":"completed","results":{"utterances":[]}}',
        )

    responses.add_callback(responses.GET, f"{TRANSCRIBE_URL}/job-1", callback=completed_callback)
    client = RTZRClient("id", "secret")
    assert client.transcribe(audio)["status"] == "completed"
    assert sum(call.request.url == AUTH_URL for call in responses.calls) == 2


@responses.activate
def test_failed_status_becomes_domain_exception():
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
    responses.add(
        responses.GET,
        f"{TRANSCRIBE_URL}/job-1",
        json={
            "id": "job-1",
            "status": "failed",
            "error": {"code": "E500", "message": "internal server error"},
        },
    )
    client = RTZRClient("id", "secret")
    with pytest.raises(TranscriptionFailed, match="E500"):
        client.wait_for_completion("job-1")


@responses.activate
def test_polling_timeout():
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


@responses.activate
@pytest.mark.parametrize(
    ("endpoint", "payload", "expected"),
    [
        ("auth", {}, "access_token"),
        ("submit", {}, "id"),
    ],
)
def test_required_response_fields(tmp_path, endpoint, payload, expected):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    if endpoint == "auth":
        responses.add(responses.POST, AUTH_URL, json=payload)
        client = RTZRClient("id", "secret")
        with pytest.raises(APIResponseError, match=expected):
            client.authenticate()
    else:
        responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
        responses.add(responses.POST, TRANSCRIBE_URL, json=payload)
        client = RTZRClient("id", "secret")
        with pytest.raises(APIResponseError, match=expected):
            client.submit(audio)


@responses.activate
def test_completed_response_requires_utterances():
    responses.add(responses.POST, AUTH_URL, json={"access_token": "token"})
    responses.add(
        responses.GET,
        f"{TRANSCRIBE_URL}/job-1",
        json={"id": "job-1", "status": "completed", "results": {}},
    )
    client = RTZRClient("id", "secret")
    with pytest.raises(APIResponseError, match="utterances"):
        client.wait_for_completion("job-1")


@responses.activate
def test_credentials_and_token_are_not_exposed_in_exception():
    client_id = "PRIVATE-CLIENT-ID"
    client_secret = "PRIVATE-CLIENT-SECRET"
    responses.add(
        responses.POST,
        AUTH_URL,
        status=401,
        json={
            "code": "H0002",
            "msg": f"{client_id} {client_secret} invalid",
        },
    )
    client = RTZRClient(client_id, client_secret)
    with pytest.raises(APIRequestError) as captured:
        client.authenticate()
    message = str(captured.value)
    assert client_id not in message
    assert client_secret not in message
