# RTZR STT CLI

FLEURS 한국어 음성을 자동으로 준비해 RTZR Batch STT API를 바로 시험하고, JSON·TXT·SRT 저장과 exact CER 평가까지 한 흐름으로 확인하는 작은 Python CLI입니다. 별도 평가 데이터를 직접 내려받거나 배치할 필요가 없습니다.

## Quickstart

Python 3.10 이상과 [uv](https://docs.astral.sh/uv/)를 권장합니다.

~~~bash
git clone https://github.com/KimSooHyo/rtzr-stt-cli.git
cd rtzr-stt-cli
uv sync --locked
cp .env.example .env
~~~

`.env`에 RTZR 개발자 콘솔에서 발급받은 값을 입력합니다. 환경 변수는 dotenv 파일보다 우선합니다.

~~~dotenv
STT_CLIENT_ID=your-client-id
STT_CLIENT_SECRET=your-client-secret
~~~

## FLEURS로 실제 STT 확인

첫 실행은 FLEURS 한국어 validation 한 건으로 확인합니다. 데이터는 고정한 Hugging Face revision에서 자동으로 내려받아 캐시하므로 음원 파일을 따로 준비하지 않아도 됩니다.

~~~bash
uv run --locked rtzr-stt evaluate \
  --output-dir outputs/fleurs-smoke
~~~

이 명령은 인증, 오디오 multipart POST, 완료까지의 GET polling을 실제로 수행합니다. 원시 응답뿐 아니라 사람이 확인할 TXT·SRT와 FLEURS 정답을 사용한 CER도 함께 저장합니다.

~~~text
outputs/fleurs-smoke/
├── manifest.json
├── summary.json
└── <sample-id>/
    ├── response.json
    ├── transcript.txt
    ├── transcript.srt
    └── metrics.json
~~~

출력 경로는 존재하지 않거나 비어 있어야 하며, 재실행할 때는 새 경로를 사용합니다.

## FLEURS 50건 평가로 확장

한 건으로 STT 흐름을 확인한 뒤 같은 명령의 표본 수만 늘려 corpus CER을 계산할 수 있습니다.

~~~bash
uv run --locked rtzr-stt evaluate \
  --samples 50 \
  --output-dir results/fleurs
~~~

프로그램은 고정한 `google/fleurs` revision에서 한국어 `dev.tsv`와 `dev.tar.gz`를 내려받고, 지정한 validation 앞 50행을 정답과 오디오로 함께 사용합니다. `dev.tsv` 전체와 선택 표본을 검증한 뒤에만 credential을 읽고 RTZR client를 만듭니다. 50개는 총 657.12초이며 고정된 15분 제한을 넘는 선택은 API 호출 전에 거부됩니다. FLEURS 실행마다 새 출력 경로를 사용해야 합니다.

최초 FLEURS 실행에는 Hugging Face 파일 다운로드가 필요합니다. 이후에는 `huggingface_hub`의 로컬 캐시를 재사용합니다. 캐시 원본은 수정하지 않으며 선택된 float WAV만 임시 디렉터리에서 16kHz mono PCM16 WAV로 변환합니다. 임시 파일은 실행이 끝나면 삭제됩니다.

`manifest.json`은 사용자가 만드는 입력이 아니라 데이터 revision, 선택 행, 원본·업로드 WAV·정답 hash를 남기는 자동 기록입니다. `summary.json`에는 데이터 출처, manifest·고정 API 설정 hash, 총 길이, 표본별 오류 수와 corpus micro CER이 기록됩니다.

CER은 FLEURS의 ASR용 `transcription`과 RTZR utterance를 단일 공백으로 연결한 문자열을 애플리케이션의 추가 정규화 없이 비교합니다. JiWER의 기본 앞뒤 공백 제거도 사용하지 않습니다. 대표값은 파일별 CER 평균이 아니라 모든 표본의 오류 수와 정답 문자 수를 합산한 corpus micro CER입니다. 자세한 기준과 실제 확인 결과는 [평가 방법](docs/evaluation.md)에 있습니다.

2026-07-16 실제 실행에서는 validation 앞 50개(657.12초)의 정답 3,194자에서 오류 279개가 집계되어 corpus micro CER은 약 8.74%였습니다. 이는 API와 평가 흐름을 확인한 결과이며 제품 전체 정확도를 대표하지 않습니다.

## 내 음원 전사(선택)

직접 준비한 음원을 README와 같은 위치에 두면 범용 `transcribe` 명령도 확인할 수 있습니다.

~~~bash
uv run --locked rtzr-stt transcribe sample.wav \
  --format all \
  --output-dir outputs/sample
~~~

UTF-8 정답 `sample.txt`가 있다면 `--reference sample.txt`를 추가해 `metrics.json`도 만들 수 있습니다. 정답 파일은 읽을 때 앞뒤 공백만 제거하며 내부 공백, 문장부호와 영문 대소문자는 그대로 CER 오류에 반영됩니다. 샘플 음원과 정답은 공개 저장소에 포함하지 않습니다.

## FLEURS 출처와 변경 사항

평가 데이터는 [Google FLEURS 데이터셋](https://huggingface.co/datasets/google/fleurs)의 한국어 validation split이며 [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)로 제공됩니다. 이 프로젝트는 출처와 license를 자동 manifest에 기록하고, 변경 사항을 밝히기 위해 선택된 원본 float WAV를 API 업로드용 16kHz mono PCM16 WAV로 변환한다고 명시합니다. 음성·정답·변환 파일 자체는 저장소에 포함하지 않습니다.

FLEURS 한국어는 일반 읽기 음성입니다. 회의·자연 대화, 장시간 문맥, 화자 분리 또는 제품 전체 성능을 대표하는 benchmark로 해석하면 안 됩니다.

## uv가 없는 환경

[uv 공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를 따르는 것이 가장 간단합니다. 설치할 수 없다면 표준 가상환경도 사용할 수 있습니다.

~~~bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
~~~

이 경우 `uv run --locked rtzr-stt` 대신 `rtzr-stt`를 사용합니다. 다만 `uv.lock`으로 고정한 개발 환경까지 재현되지는 않습니다.

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

자동 테스트는 로컬 FLEURS 형식 fixture와 mock downloader/client만 사용하며 네트워크나 실제 API를 호출하지 않습니다. 실제 API smoke test는 FLEURS validation 한 건을 자동으로 준비해 별도로 실행합니다.

~~~bash
make smoke-test
~~~

다시 실행할 때는 `make smoke-test SMOKE_OUTPUT=build/fleurs-smoke-2`처럼 새 출력 경로를 지정합니다.

## 오류 해결

- credential 오류: `.env` 또는 환경 변수의 두 값을 확인합니다.
- FLEURS 다운로드 오류: 네트워크와 Hugging Face 캐시 쓰기 권한을 확인합니다.
- 결과 디렉터리 오류: 이전 결과가 섞이지 않도록 존재하지 않거나 비어 있는 새 경로를 지정합니다.
- HTTP 401 또는 H0002: 발급 정보를 확인합니다. 조회 중 401은 한 번만 재인증합니다.
- HTTP 429: 조회 GET은 재시도하고, 생성 POST는 동시 처리 제한 코드 A0002일 때만 재시도합니다.
- 시간 초과: `--timeout`은 polling 대기 제한입니다. 다시 실행하면 완료 표본도 포함해 새 작업을 생성하므로 사용량을 먼저 확인합니다.
- H0010: 단건 전사 파일이 공식 지원 형식인지 확인합니다. FLEURS 흐름은 검증된 PCM16 WAV만 업로드합니다.

기본 polling 간격은 5초, 파일별 대기 제한은 3,600초입니다. `--poll-interval`, `--timeout`으로 바꿀 수 있습니다. 요청·응답과 재시도 기준은 [API 계약](docs/api-contract.md)에 정리했습니다.

## 데이터와 보안

FLEURS 캐시, 변환 음원, 자동 manifest, 원시 응답, transcript, 파일별 metrics와 summary는 공개 저장소에 올리지 않습니다. 이 파일에는 발화 내용이나 식별 정보가 들어갈 수 있으며 도구가 자동 암호화·익명화를 제공하지 않으므로 로컬 접근 권한과 보관 기간을 직접 관리해야 합니다. RTZR 안내에 따르면 Batch STT 결과는 서버에서 3일간 보관된 뒤 삭제됩니다. 이 정책은 CLI가 저장한 로컬 산출물에는 적용되지 않습니다.

## 참고 자료와 오픈소스

- API 계약: RTZR [인증](https://developers.rtzr.ai/docs/authentications/), [일반 STT](https://developers.rtzr.ai/docs/stt-file/), [처리량 제한](https://developers.rtzr.ai/docs/en/rate_limit/), [결과 보관 안내](https://developers.rtzr.ai/docs/)
- 데이터: [FLEURS 데이터 카드](https://huggingface.co/datasets/google/fleurs), [FLEURS 논문](https://arxiv.org/abs/2205.12446), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- 실행 의존성: [Hugging Face Hub](https://huggingface.co/docs/huggingface_hub/), [SoundFile](https://python-soundfile.readthedocs.io/), [Requests](https://requests.readthedocs.io/en/latest/), [python-dotenv](https://pypi.org/project/python-dotenv/), [JiWER](https://jitsi.github.io/jiwer/)
- 개발 도구: [uv](https://docs.astral.sh/uv/), [pytest](https://docs.pytest.org/en/stable/), [responses](https://github.com/getsentry/responses), [Ruff](https://docs.astral.sh/ruff/), [Hatchling](https://hatch.pypa.io/latest/config/build/)

## 더 읽기

- [코드 개요](docs/code-overview.md): 읽는 순서, 데이터 흐름, 설계 선택
- [API 계약](docs/api-contract.md): 인증, 요청, polling, retry
- [평가 방법](docs/evaluation.md): 표본, exact CER, 결과와 한계

이 프로젝트는 로컬 단일 프로세스 실행용입니다. 중단 작업 이어받기, 완료 결과 자동 재사용, 같은 출력 디렉터리의 동시 실행과 이전 manifest CLI 호환은 지원하지 않습니다.
