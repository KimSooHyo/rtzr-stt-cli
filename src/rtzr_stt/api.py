from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from rtzr_stt.config import BASE_URL, DEFAULT_TRANSCRIBE_CONFIG, canonical_config_json

TRANSIENT_GET_STATUSES = {429, 500, 502, 503, 504}
BACKOFF_SECONDS = (1.0, 2.0, 4.0)


class RTZRError(RuntimeError):
    """Base exception for safe, user-facing RTZR errors."""


class AuthenticationError(RTZRError):
    pass


class APIRequestError(RTZRError):
    def __init__(self, operation: str, status: int, code: str | None = None) -> None:
        suffix = f", code={code}" if code else ""
        super().__init__(f"{operation} 요청 실패: HTTP {status}{suffix}")
        self.operation = operation
        self.status = status
        self.code = code


class APIResponseError(RTZRError):
    pass


class TranscriptionFailed(RTZRError):
    pass


class TranscriptionTimeout(RTZRError):
    pass


def _response_code(response: requests.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    return str(code) if code is not None else None


def _json_object(response: requests.Response, operation: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise APIResponseError(f"{operation} 응답이 유효한 JSON이 아닙니다.") from exc
    if not isinstance(payload, dict):
        raise APIResponseError(f"{operation} 응답의 최상위 형식이 객체가 아닙니다.")
    return payload


class RTZRClient:
    """Small synchronous client for the official batch transcription API."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
        request_timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._request_timeout = request_timeout
        self._sleep = sleep
        self._monotonic = monotonic
        self._access_token: str | None = None

    def authenticate(self) -> str:
        try:
            response = self._session.post(
                f"{self._base_url}/v1/authenticate",
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout=self._request_timeout,
            )
        except requests.RequestException as exc:
            raise AuthenticationError("인증 서버에 연결하지 못했습니다.") from exc
        if response.status_code != 200:
            raise APIRequestError("인증", response.status_code, _response_code(response))
        payload = _json_object(response, "인증")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise APIResponseError("인증 응답에 access_token이 없습니다.")
        self._access_token = token
        return token

    def _authorized_request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        retry_get: bool,
        **kwargs: Any,
    ) -> requests.Response:
        if self._access_token is None:
            self.authenticate()

        reauthenticated = False
        retry_count = 0
        base_headers = dict(kwargs.pop("headers", {}))
        while True:
            headers = dict(base_headers)
            headers["Authorization"] = f"Bearer {self._access_token}"
            try:
                response = self._session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=self._request_timeout,
                    **kwargs,
                )
            except requests.RequestException as exc:
                if retry_get and retry_count < len(BACKOFF_SECONDS):
                    self._sleep(BACKOFF_SECONDS[retry_count])
                    retry_count += 1
                    continue
                raise APIRequestError(operation, 0, "NETWORK_ERROR") from exc

            if response.status_code == 401 and not reauthenticated:
                self.authenticate()
                reauthenticated = True
                for file_part in kwargs.get("files", {}).values():
                    file_object = file_part[1] if isinstance(file_part, tuple) else file_part
                    if hasattr(file_object, "seek"):
                        file_object.seek(0)
                continue

            if (
                retry_get
                and response.status_code in TRANSIENT_GET_STATUSES
                and retry_count < len(BACKOFF_SECONDS)
            ):
                self._sleep(BACKOFF_SECONDS[retry_count])
                retry_count += 1
                continue

            if not 200 <= response.status_code < 300:
                raise APIRequestError(operation, response.status_code, _response_code(response))
            return response

    def submit(
        self,
        audio_path: str | Path,
        config: dict[str, Any] | None = None,
    ) -> str:
        path = Path(audio_path)
        if not path.is_file():
            raise FileNotFoundError(f"오디오 파일을 찾을 수 없습니다: {path}")
        request_config = config or DEFAULT_TRANSCRIBE_CONFIG
        try:
            with path.open("rb") as audio:
                response = self._authorized_request(
                    "POST",
                    f"{self._base_url}/v1/transcribe",
                    operation="전사 생성",
                    retry_get=False,
                    headers={"accept": "application/json"},
                    data={"config": canonical_config_json(request_config)},
                    files={"file": (path.name, audio)},
                )
        except OSError as exc:
            raise RTZRError(f"오디오 파일을 읽을 수 없습니다: {path}") from exc
        payload = _json_object(response, "전사 생성")
        transcribe_id = payload.get("id")
        if not isinstance(transcribe_id, str) or not transcribe_id:
            raise APIResponseError("전사 생성 응답에 id가 없습니다.")
        return transcribe_id

    def get(self, transcribe_id: str) -> dict[str, Any]:
        response = self._authorized_request(
            "GET",
            f"{self._base_url}/v1/transcribe/{transcribe_id}",
            operation="전사 조회",
            retry_get=True,
            headers={"accept": "application/json"},
        )
        return _json_object(response, "전사 조회")

    def wait_for_completion(
        self,
        transcribe_id: str,
        *,
        poll_interval: float = 5.0,
        timeout: float = 1800.0,
    ) -> dict[str, Any]:
        if poll_interval <= 0:
            raise ValueError("poll_interval은 0보다 커야 합니다.")
        if timeout <= 0:
            raise ValueError("timeout은 0보다 커야 합니다.")

        started = self._monotonic()
        while True:
            if self._monotonic() - started >= timeout:
                raise TranscriptionTimeout(
                    f"전사 완료를 {timeout:g}초 동안 기다렸지만 시간 초과되었습니다."
                )
            payload = self.get(transcribe_id)
            status = payload.get("status")
            if status == "completed":
                results = payload.get("results")
                utterances = results.get("utterances") if isinstance(results, dict) else None
                if not isinstance(utterances, list):
                    raise APIResponseError("완료 응답에 results.utterances 배열이 없습니다.")
                return payload
            if status == "failed":
                error = payload.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                safe_code = str(code) if code is not None else "UNKNOWN"
                raise TranscriptionFailed(f"전사 처리 실패: code={safe_code}")
            if status != "transcribing":
                raise APIResponseError(f"알 수 없는 전사 상태입니다: {status!r}")
            self._sleep(poll_interval)

    def transcribe(
        self,
        audio_path: str | Path,
        *,
        config: dict[str, Any] | None = None,
        poll_interval: float = 5.0,
        timeout: float = 1800.0,
    ) -> dict[str, Any]:
        transcribe_id = self.submit(audio_path, config)
        return self.wait_for_completion(
            transcribe_id,
            poll_interval=poll_interval,
            timeout=timeout,
        )
