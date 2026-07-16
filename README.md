# RTZR STT CLI

RTZR Batch STT API로 오디오를 전사해 JSON·TXT·SRT로 저장하고, 정답이 있으면 철자형 CER을 계산하는 작은 Python CLI입니다.

## Quickstart

Python 3.10 이상과 [uv](https://docs.astral.sh/uv/)를 권장합니다.

~~~bash
git clone https://github.com/KimSooHyo/rtzr-stt-cli.git
cd rtzr-stt-cli
uv sync --locked
cp .env.example .env
~~~

`.env`에 RTZR 개발자 콘솔에서 발급받은 값을 입력합니다.

~~~dotenv
STT_CLIENT_ID=your-client-id
STT_CLIENT_SECRET=your-client-secret
~~~

`sample.wav`를 `README.md`와 같은 위치에 둡니다.

~~~text
rtzr-stt-cli/
├── README.md
├── sample.wav
└── sample.txt     # CER용 정답이 있을 때만 필요
~~~

저장소 루트에서 첫 전사를 실행합니다.

~~~bash
uv run --locked rtzr-stt transcribe sample.wav \
  --format all \
  --output-dir outputs/sample
~~~

`outputs/sample/`에 `response.json`, `transcript.txt`, `transcript.srt`가 생성됩니다.

## uv가 없는 환경

가능하면 [uv 공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를 따라 설치합니다. 설치할 수 없다면 표준 가상환경으로 CLI를 실행할 수 있습니다.

~~~bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
~~~

이후 `uv run --locked rtzr-stt` 대신 `rtzr-stt`를 사용합니다. 이 방법은 `uv.lock`을 사용하지 않으므로 잠긴 개발 환경까지 재현하려면 uv가 필요합니다.

환경 변수 `STT_CLIENT_ID`, `STT_CLIENT_SECRET`은 dotenv 파일보다 우선합니다.

## CER 계산

UTF-8 정답을 함께 지정하면 같은 전사 결과의 철자형 CER과 `metrics.json`을 만듭니다. CER은 낮을수록 좋으며 정확도 백분율과는 다른 지표입니다.

~~~bash
uv run --locked rtzr-stt transcribe sample.wav \
  --format all \
  --output-dir outputs/sample-with-cer \
  --reference sample.txt
~~~

정답 파일이 없다면 `--reference`를 생략하면 됩니다.

## 여러 WAV 평가

새 오디오는 `data/audio/`, 정답은 `data/references/`에 두는 것을 권장합니다. 두 경로와 루트의 `sample.wav`, `sample.txt`는 Git에서 제외됩니다.

~~~text
data/
├── audio/meeting-01.wav
├── references/meeting-01.txt
└── manifest.csv
~~~

`data/manifest.example.csv`를 복사해 각 행을 작성합니다. 오디오와 정답 경로는 manifest 파일 위치를 기준으로 해석합니다.

~~~csv
sample_id,audio_path,reference_path,session_id,stratum
sample-001,audio/meeting-01.wav,references/meeting-01.txt,session-01,clean
~~~

~~~bash
cp data/manifest.example.csv data/manifest.csv
uv run --locked rtzr-stt evaluate data/manifest.csv \
  --output-dir results/evaluation \
  --max-audio-minutes 15
~~~

`evaluate`는 WAV 유효성, 정답, 안전한 `sample_id`, 전체 길이를 먼저 검사한 뒤 모든 행을 순서대로 전사합니다. 대표값은 파일별 CER의 평균이 아니라 전체 문자 오류를 합산한 corpus micro CER입니다.

각 표본 디렉터리에는 전사 산출물이 저장됩니다. 루트 `summary.json`에는 표본 수, 총 길이, manifest·설정·입력 hash, corpus CER, 층별·파일별 지표가 저장됩니다. 자세한 규칙은 [평가 문서](docs/evaluation.md)를 참고하세요.

## 테스트

~~~bash
make check
~~~

`make`가 없다면 다음 명령을 차례로 실행합니다.

~~~bash
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
~~~

자동 테스트는 실제 API를 호출하지 않습니다. 실제 API 한 건은 이용 권한이 있는 짧은 음원으로 명시적으로 실행합니다.

~~~bash
make smoke-test SMOKE_AUDIO=sample.wav
~~~

## 오류 해결

- credential 오류: `.env` 또는 환경 변수의 두 값을 확인합니다.
- HTTP 401 또는 H0002: 발급 정보를 확인합니다. 조회 중 401은 한 번만 재인증합니다.
- HTTP 429: 조회 GET은 재시도하고, 생성 POST는 작업 미접수를 뜻하는 A0002일 때만 재시도합니다.
- 시간 초과: `--timeout`은 polling 대기 제한이며, 늘려서 다시 실행하면 새 작업이 생성됩니다.
- H0010: 공식 지원 형식인지 확인합니다. `evaluate`는 길이 검증을 위해 WAV만 받습니다.

기본 polling 간격은 5초, 파일별 대기 제한은 3,600초입니다. 바꾸려면 `--poll-interval`, `--timeout`을 사용합니다. 요청·응답과 재시도 기준은 [API 계약 문서](docs/api-contract.md)에 정리했습니다.

## 데이터와 보안

음원, 정답, 실제 manifest와 결과 파일은 공개 저장소에 올리지 않습니다.

`response.json`, transcript, `metrics.json`, `summary.json`에는 발화 내용이나 파일 식별 정보가 들어갈 수 있습니다. 이 도구는 결과를 암호화하거나 익명화하지 않으므로 로컬 접근 권한과 보관 기간을 직접 관리해야 합니다.

AIHub 자료를 사용하려면 해당 데이터의 이용정책과 외부 API 처리 허용 범위를 먼저 확인해야 합니다. 이 프로젝트에서는 AIHub 원음을 외부 API에 다시 업로드할 수 있다는 별도 허가를 확인하지 못했으므로, 비공개 평가 수치를 현재 코드의 성과로 제시하지 않습니다.

## 참고 자료와 사용한 오픈소스

- API 계약: RTZR [인증](https://developers.rtzr.ai/docs/authentications/), [일반 STT](https://developers.rtzr.ai/docs/stt-file/), [처리량 제한](https://developers.rtzr.ai/docs/en/rate_limit/), [결과 보관 안내](https://developers.rtzr.ai/docs/)
- 실행 의존성: [Requests](https://requests.readthedocs.io/en/latest/), [python-dotenv](https://pypi.org/project/python-dotenv/), [JiWER](https://jitsi.github.io/jiwer/)
- 개발 도구: [uv](https://docs.astral.sh/uv/), [pytest](https://docs.pytest.org/en/stable/), [responses](https://github.com/getsentry/responses), [Ruff](https://docs.astral.sh/ruff/), [Hatchling](https://hatch.pypa.io/latest/config/build/)
- 데이터 관련 자료: 과학기술정보통신부·한국지능정보사회진흥원의 AI Hub 사업결과물인 [주요 영역별 회의 음성인식 데이터](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=464), [이용정책](https://aihub.or.kr/intrcn/guid/usagepolicy.do?currMenu=151&topMenu=105), [한국어 음성 전사 규칙 v1.0](https://aihub.or.kr/aihubnews/notice/view.do?currMenu=132&nttSn=9746&pageIndex=1&topMenu=103)

## 더 읽기

- [코드 개요](docs/code-overview.md): 읽는 순서, 데이터 흐름, 설계 선택
- [API 계약](docs/api-contract.md): 인증, 요청, polling, retry
- [평가 방법](docs/evaluation.md): 표본, 정규화, CER, 결과와 한계

이 프로젝트는 로컬 단일 프로세스 실행용입니다. 중단 작업 이어받기, 완료 결과 자동 재사용, 같은 출력 디렉터리의 동시 실행은 지원하지 않습니다.
