# API contract traceability

기준일: 2026-07-15

| 공식 계약 | 구현 위치 | 검증 위치 | 사용자 문서 |
|---|---|---|---|
| POST /v1/authenticate, form-urlencoded client_id·client_secret | src/rtzr_stt/api.py authenticate | tests/test_api.py auth_and_multipart_contract | README credential |
| 응답 access_token, Bearer JWT | src/rtzr_stt/api.py authenticate·authorized_request | required_response_fields, 401_reauthenticates_once | README 오류 해결 |
| POST /v1/transcribe, multipart file·config | src/rtzr_stt/api.py submit | auth_and_multipart_contract | README 고정 API 설정 |
| GET /v1/transcribe/{id} | src/rtzr_stt/api.py get | polling 관련 contract tests | README Quickstart |
| transcribing·completed·failed | src/rtzr_stt/api.py wait_for_completion | failed_status, polling_timeout, completed_response_requires_utterances | README 오류 해결 |
| utterances start_at·duration·msg | src/rtzr_stt/formatters.py | tests/test_metrics_and_formatters.py | README 출력 설명 |
| 권장 polling 5초 | src/rtzr_stt/cli.py 기본값 | auth_and_multipart_contract | README 오류 해결 |
| 조회 요청 제한 429 | src/rtzr_stt/api.py GET backoff | get_429_uses_exponential_backoff | README 오류 해결 |

## 요청 정책

- 인증 토큰은 프로세스 메모리에서 재사용한다.
- 인증된 요청의 401은 한 번만 재인증한다.
- 생성 POST의 연결 실패는 작업 생성 여부가 모호하므로 자동 재전송하지 않는다.
- 조회 GET의 연결 실패, 429, 5xx는 1·2·4초 backoff로 최대 세 번 재시도한다.
- credential, token, 서버 오류 메시지 원문은 예외나 로그에 넣지 않는다. HTTP status와 공식 오류 code만 노출한다.

## 고정 설정

고정 설정의 단일 소스는 src/rtzr_stt/config.py의 DEFAULT_TRANSCRIBE_CONFIG다. 요청 본문과 resume cache key는 같은 canonical JSON을 사용한다. 테스트는 multipart 안의 canonical 설정 전체를 확인한다.

## 공식 문서

- [인증 가이드](https://developers.rtzr.ai/docs/authentications/)
- [일반 STT](https://developers.rtzr.ai/docs/stt-file/)
- [처리량 제한](https://developers.rtzr.ai/docs/en/rate_limit/)
