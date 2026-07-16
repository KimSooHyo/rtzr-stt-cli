# 코드 개요

이 문서는 처음 코드를 읽는 개발자가 전체 흐름을 빠르게 이해하기 위한 안내다.

## 권장 읽기 순서

1. `cli.py`: 두 명령의 사용자 인터페이스와 전체 흐름
2. `config.py`: credential 우선순위와 고정 API 설정
3. `api.py`: 인증, 작업 생성, polling, retry
4. `formatters.py`, `metrics.py`: TXT·SRT 변환과 exact CER
5. `evaluation.py`: FLEURS 준비, 순차 실행, 집계
6. `io.py`: hash와 원자적 파일 저장

## 모듈 역할

| 모듈 | 책임 |
|---|---|
| `cli.py` | argparse 인터페이스, 모듈 조합, 사용자 메시지 |
| `config.py` | 환경 변수·dotenv credential과 고정 config JSON/hash |
| `api.py` | RTZR HTTP 계약, 응답 검증, 제한적 retry, polling |
| `formatters.py` | 완료 응답의 utterance를 TXT·SRT·가설 문자열로 변환 |
| `metrics.py` | 정규화 없는 파일별·corpus exact CER 계산 |
| `evaluation.py` | 고정 FLEURS metadata/archive 검증, PCM 변환, 자동 manifest, 순차 전사·집계 |
| `io.py` | SHA-256, UTF-8·JSON 임시 파일 작성 후 `os.replace` |

HTTP 상태와 최상위 응답 계약은 `api.py`, utterance field와 SRT 표현은 `formatters.py`, 비교 의미는 `metrics.py`, 데이터 선택과 검증은 `evaluation.py`에서 결정한다.

## 단건 전사 흐름

~~~text
CLI 인자·파일 확인
    → 정답이 있으면 앞뒤 공백만 제거해 검사
    → 출력 경로가 새 경로이거나 빈 디렉터리인지 검사
    → credential 읽기
    → POST 인증
    → multipart POST 전사 생성
    → GET polling
    → 완료 응답 검증
    → JSON 및 선택한 TXT·SRT 저장
    → 정답이 있으면 exact CER 저장
~~~

모호한 생성 POST 실패는 자동 재전송하지 않는다. 조회 GET은 읽기 요청이므로 일시적인 네트워크·429·5xx에 제한적으로 재시도한다. 이 차이는 중복 작업 위험을 줄이기 위한 것이다.

## FLEURS STT·평가 흐름

~~~text
고정 revision의 한국어 dev.tsv 다운로드·캐시
    → 전체 metadata·숫자 WAV 파일명과 앞 N행·900초 제한 검증
    → dev.tar.gz 다운로드·캐시
    → tar 경로와 선택 float WAV 구조 검증
    → 임시 16kHz mono PCM16 WAV 변환, 구조·비무음 검증과 hash 기록
    → credential 및 RTZR client 생성
    → 자동 manifest.json 저장
    → 각 표본을 순서대로 전사
    → 표본별 TXT·SRT·metrics 저장
    → 전체 오류 수를 합산해 summary.json 저장
    → 임시 PCM 삭제
~~~

metadata와 모든 선택 WAV 검증이 client 생성보다 앞선다. 표본은 결과 확인 전에 TSV 앞 N행으로 결정한다. 입력 음성과 정답 모두 같은 FLEURS 행을 사용하며 사용자 manifest나 별도 평가 데이터는 없다.

기본값 1인 `evaluate`는 별도 음원 준비 없이 실제 RTZR STT 요청과 산출물을 확인하는 smoke test다. 같은 흐름에서 `--samples`를 늘리면 각 파일을 순차 전사한 뒤 corpus micro CER까지 집계한다.

두 명령 모두 비어 있지 않은 output directory를 API 호출 전에 거부한다. 재실행에는 새 경로를 사용해 이전 실행의 SRT·metrics나 표본 디렉터리가 새 결과와 섞이지 않게 한다.

## 핵심 판단

- dataset repository, full revision, language, split과 API 설정을 고정한다.
- `huggingface_hub`로 원본 파일을 캐시하고 선택 표본만 `soundfile`로 변환해 `datasets[audio]`, Torch, CUDA와 시스템 FFmpeg 의존성을 피한다.
- reference와 hypothesis를 공백·문장부호·대소문자까지 그대로 비교한다. JiWER의 기본 `Strip`도 사용하지 않는다.
- 원본/업로드 WAV, 정답, metadata, 자동 manifest와 config hash를 결과에 남긴다.
- 대표값은 파일별 평균이 아니라 corpus micro CER이다.
- JSON은 `NaN`을 거부하고 같은 디렉터리의 임시 파일을 `os.replace`해 불완전한 개별 파일 노출을 줄인다.
- 자동 테스트는 로컬 fixture와 mock만 사용하며 네트워크와 실제 API를 분리한다.

## 의도적으로 포기한 보장

이 프로젝트는 한 사람이 로컬에서 실행하고 이해하는 CLI다. 다음을 지원하지 않는다.

- 중단된 원격 작업 이어받기 또는 완료 결과 재사용
- 같은 output directory의 여러 프로세스 동시 실행
- 프로세스·전원 장애까지 견디는 디렉터리 단위 transaction
- 서버 모델 revision 고정
- 이전 CSV manifest CLI 및 제거된 flag와의 호환
- 기존 결과가 있는 output directory 덮어쓰기

`os.replace`는 개별 파일만 원자적으로 교체하며 여러 산출물을 하나의 transaction으로 묶지 않는다. 평가 중 실패하면 이미 완료된 표본 산출물이 남을 수 있다. 이 출력 경로는 더 이상 비어 있지 않아 다음 실행에서 거부된다. 재실행하려면 새 경로나 빈 디렉터리를 지정해야 하며, 완료했던 표본도 cache로 재사용하지 않고 새 API 작업으로 생성한다.
