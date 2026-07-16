# 코드 개요

이 문서는 처음 코드를 읽는 개발자가 전체 흐름을 빠르게 이해하기 위한 안내다.

## 권장 읽기 순서

1. `cli.py`: 사용자 입력 검증과 두 명령의 전체 흐름
2. `config.py`: credential 우선순위와 고정 API 설정
3. `api.py`: 인증, 작업 생성, polling, retry
4. `formatters.py`, `metrics.py`: TXT·SRT 변환과 CER
5. `evaluation.py`: manifest 검증, 순차 실행, 집계
6. `io.py`: hash와 원자적 파일 저장

## 모듈 역할

| 모듈 | 책임 |
|---|---|
| `cli.py` | argparse 인터페이스, 로컬 입력 검사, 모듈 조합, 사용자 메시지 |
| `config.py` | 환경 변수·dotenv credential과 고정 config JSON/hash |
| `api.py` | RTZR HTTP 계약, 응답 검증, 제한적 retry, polling |
| `formatters.py` | 완료 응답의 utterance를 TXT·SRT·가설 문자열로 변환 |
| `metrics.py` | 한국어 철자형 정규화, 파일별·corpus CER 계산 |
| `evaluation.py` | CSV/WAV 사전 검증, 표본 순차 전사, 층별·전체 집계 |
| `io.py` | SHA-256, UTF-8·JSON 임시 파일 작성 후 `os.replace` |

각 모듈은 하나의 이유로 바뀌도록 나눴다. 예를 들어 HTTP 상태와 최상위 응답 계약은 `api.py`, utterance field와 SRT 표현은 `formatters.py`, 평가 정규화는 `metrics.py`에서 결정한다.

## 단건 전사 흐름

~~~text
CLI 인자·파일 확인
    → credential 읽기
    → POST 인증
    → multipart POST 전사 생성
    → GET polling
    → 완료 응답 검증
    → JSON 및 선택한 TXT·SRT 저장
    → 정답이 있으면 CER 저장
~~~

모호한 생성 POST 실패는 자동 재전송하지 않는다. 조회 GET은 읽기 요청이므로 일시적인 네트워크·429·5xx에 제한적으로 재시도한다. 이 차이는 중복 작업 위험과 사용 편의를 함께 고려한 것이다.

## manifest 평가 흐름

~~~text
CSV·모든 입력·WAV 길이 사전 검증
    → manifest/config/input SHA-256 고정
    → 각 표본을 순서대로 전사
    → 표본별 TXT·SRT·metrics 저장
    → 전체·층별 오류 수 합산
    → summary.json 저장
~~~

`--max-audio-minutes`로 설정한 길이 제한(기본 15분)은 API 호출 전에 확인한다. 대표값은 파일별 CER 평균이 아니라 모든 오류와 정답 문자를 합친 corpus micro CER이다. `session_id`와 `stratum`은 결과를 진단하기 위한 그룹 정보이며 API 요청에는 쓰지 않는다.

## 핵심 판단

- 설정을 코드에서 고정해 평가 간 비교 조건을 유지한다.
- reference와 hypothesis에 같은 정규화를 적용하고 숫자는 보존한다.
- 입력 hash를 결과에 남겨 어떤 로컬 파일로 계산했는지 설명한다.
- JSON은 `NaN`을 거부하고, 같은 디렉터리의 임시 파일을 `os.replace`해 완성되지 않은 파일 노출을 줄인다.
- 자동 테스트는 mock HTTP만 사용하고 실제 비용이 드는 smoke test를 분리한다.

## 의도적으로 포기한 보장

초기 구현에는 중단 후 이어받기와 완료 cache가 있었지만 상태 관리 비용이 커 제거했다. 검토 과정에서 제안된 checkpoint, output lock, upload snapshot, 디렉터리 `fsync`도 최종 범위에는 넣지 않았다. 이 프로젝트는 배포 서비스가 아니라 한 사람이 로컬에서 실행하고 이해하는 CLI이므로, 학습·검토 비용이 핵심 흐름보다 커지는 기능은 제외했다.

따라서 다음을 지원하지 않는다.

- 중단된 원격 작업 이어받기 또는 완료 결과 재사용
- 같은 output directory에 여러 프로세스 동시 실행
- 프로세스·전원 장애까지 견디는 디렉터리 단위 영속성
- hash 계산 뒤 업로드 직전까지 입력 변경을 막는 snapshot
- 예전 `job.json`·`cache.json`이나 제거된 CLI flag와의 호환

사용자는 한 번에 한 프로세스를 실행하고, 새 평가에는 새 output directory를 사용하는 것을 전제로 한다. `--timeout`은 개별 HTTP 요청의 엄밀한 deadline이 아니며, 실패하면 종료 코드를 확인한 뒤 새 작업으로 다시 실행한다. 이 제한은 숨은 결함이 아니라 현재 사용 범위에 맞춘 명시적인 trade-off다.

`os.replace`는 개별 파일만 원자적으로 교체하며 여러 산출물을 하나의 transaction으로 묶지는 않는다. 평가 중 실패하면 이미 완료된 표본 산출물이 남을 수 있지만 재개 상태로 사용하지 않는다.
