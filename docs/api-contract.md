# API 계약

기준일: 2026-07-15

구현 기준은 RTZR의 [인증 가이드](https://developers.rtzr.ai/docs/authentications/), [일반 STT](https://developers.rtzr.ai/docs/stt-file/), [처리량 제한](https://developers.rtzr.ai/docs/en/rate_limit/)이다.

## 1. 인증

`POST /v1/authenticate`에 `application/x-www-form-urlencoded` 형식의 `client_id`, `client_secret`을 보낸다. 응답의 비어 있지 않은 `access_token`을 메모리에 보관하고 이후 요청에 `Authorization: Bearer <token>`을 사용한다.

credential과 token은 파일, 로그, 예외 메시지에 기록하지 않는다. 인증 실패에는 HTTP status와 공식 오류 code만 노출한다.

## 2. 전사 생성

`POST /v1/transcribe`에 다음 multipart field를 보낸다.

- `file`: 오디오 binary. 로컬 파일명 대신 확장자만 유지한 `audio.wav` 같은 이름을 사용한다.
- `config`: `DEFAULT_TRANSCRIBE_CONFIG`를 JSON 문자열로 직렬화한 값

성공 응답은 비어 있지 않은 문자열 `id`를 포함해야 한다. 이 ID로 결과를 조회한다.

요청의 고정 설정은 `src/rtzr_stt/config.py`가 단일 소스다. JSON은 key를 정렬하고 `NaN`·무한대를 거부해 같은 설정의 hash가 안정적으로 계산되도록 한다.

## 3. 결과 조회

`GET /v1/transcribe/{id}`를 기본 5초 간격으로 호출한다.

- `transcribing`: 기다린 뒤 다시 조회
- `completed`: `results.utterances`가 배열인지 확인하고 반환
- `failed`: 서버 오류 code만 포함한 전사 실패로 변환
- 그 밖의 상태나 필수 field 누락: 계약 밖 응답으로 처리

TXT는 각 utterance의 `msg`를 사용한다. SRT는 정수형 millisecond `start_at`, `duration`으로 시작·종료 시각을 만든다. 음수 timestamp나 잘못된 field 형식은 결과 파일을 만들기 전에 거부한다.

`--timeout`은 polling loop가 다음 조회를 시작할지 판단하는 대기 제한이다. 인증과 각 HTTP 요청에는 별도의 30초 request timeout을 사용하므로 한 요청이나 retry backoff만큼 전체 wall-clock 시간이 더 길어질 수 있다.

## 4. 재시도 원칙

- 인증된 요청이 401이면 token을 새로 발급해 한 번만 다시 요청한다.
- 조회 GET의 네트워크 오류, 429, 500·502·503·504는 1·2·4초 간격으로 최대 세 번 재시도한다.
- 생성 POST는 서버가 작업을 받았는지 불명확한 네트워크 오류나 5xx에서 재전송하지 않는다.
- 생성 POST의 429 중 작업이 수락되지 않았음을 뜻하는 A0002만 파일을 되감고 1·2·4초 간격으로 최대 세 번 재시도한다. 사용량 초과 A0001은 재시도하지 않는다.

이 비대칭 정책은 일시적인 조회 실패에는 대응하되, 모호한 POST를 반복해 중복 작업을 만드는 위험을 줄이기 위한 선택이다.

## 5. 검증 위치

| 계약 | 구현 | 자동 검증 |
|---|---|---|
| form 인증과 access token | `api.py` | 인증 요청·필수 field test |
| multipart file·config | `api.py`, `config.py` | wire contract test |
| Bearer와 401 재인증 | `api.py` | 재인증·file rewind test |
| polling 상태와 timeout | `api.py` | completed·failed·unknown·timeout test |
| GET와 제한적 POST retry | `api.py` | network·429·5xx·A0002 test |
| utterance TXT·SRT 변환 | `formatters.py` | golden·malformed response test |

자동 테스트는 mock HTTP 응답을 사용하며 실제 API를 호출하지 않는다. 서버 정책 변경 가능성은 짧은 live smoke test로 별도 확인한다.
