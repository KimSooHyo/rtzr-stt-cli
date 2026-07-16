# RTZR STT CLI

FLEURS 한국어 음성을 자동으로 준비해 RTZR Batch STT API를 호출하고, JSON·TXT·SRT 저장과 exact CER 평가까지 수행하는 Python CLI입니다.

## Quickstart

Python 3.10 이상과 [uv](https://docs.astral.sh/uv/)를 권장합니다.

~~~bash
git clone https://github.com/KimSooHyo/rtzr-stt-cli.git
cd rtzr-stt-cli
uv sync --locked
cp .env.example .env
~~~

`.env`에 RTZR 개발자 콘솔에서 발급받은 값을 입력합니다. 환경 변수는 `.env`보다 우선합니다.

~~~dotenv
STT_CLIENT_ID=your-client-id
STT_CLIENT_SECRET=your-client-secret
~~~

FLEURS 한국어 validation 한 건으로 실제 STT 흐름을 확인합니다.

~~~bash
uv run --locked rtzr-stt evaluate \
  --output-dir outputs/fleurs-smoke
~~~

첫 실행에는 약 120 MiB의 한국어 validation 오디오 압축파일 다운로드가 필요합니다. 이후에는 고정한 Hugging Face revision의 로컬 캐시를 재사용합니다.

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

출력 경로는 존재하지 않거나 비어 있어야 합니다. 재실행할 때는 새 경로를 사용합니다.

<details>
<summary>uv가 없는 환경</summary>

~~~bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
~~~

이 경우 `uv run --locked rtzr-stt` 대신 `rtzr-stt`를 사용합니다. 다만 `uv.lock`으로 고정한 환경까지 재현되지는 않습니다.

</details>

## 50개 평가로 확장

같은 흐름을 50개 표본으로 확장해 corpus micro CER를 계산합니다.

~~~bash
uv run --locked rtzr-stt evaluate \
  --samples 50 \
  --output-dir results/fleurs
~~~

- 입력: 고정한 FLEURS revision의 한국어 validation 앞 50개·657.12초
- 기록: 데이터·선택·입력 hash는 `manifest.json`, 집계 결과는 `summary.json`
- 결과: 정답 3,194자 중 오류 279개, corpus micro exact CER 약 8.74%

CER는 추가 정규화 없이 공백·문장부호·대소문자 차이까지 포함합니다. 이 결과는 읽기 음성으로 API와 평가 흐름을 확인한 값이며 제품 전체 정확도를 대표하지 않습니다. 자세한 기준은 [평가 방법](docs/evaluation.md)에 있습니다.

## 내 음원 전사

권한이 있는 음원으로 범용 전사 명령을 실행할 수 있습니다.

~~~bash
uv run --locked rtzr-stt transcribe sample.wav \
  --format all \
  --output-dir outputs/sample
~~~

UTF-8 정답이 있다면 `--reference sample.txt`를 추가해 `metrics.json`도 생성할 수 있습니다. 샘플 음원과 정답은 공개 저장소에 포함하지 않습니다.

## 테스트

~~~bash
make check
~~~

lockfile, Ruff lint·format과 83개 unit·fixture·mock 테스트를 확인합니다. 자동 테스트는 네트워크나 실제 API를 호출하지 않습니다. 한 건의 live smoke test는 `make smoke-test`로 별도 실행합니다.

## 실행 시 참고

- FLEURS 다운로드에는 네트워크와 Hugging Face 캐시 쓰기 권한이 필요합니다.
- 중단 작업 resume와 완료 결과 재사용은 지원하지 않습니다.
- polling 간격과 timeout은 `--poll-interval`, `--timeout`으로 바꿀 수 있습니다.
- 인증·HTTP 오류와 재시도 기준은 [API 계약](docs/api-contract.md)을 참고하세요.

## 데이터와 보안

- 평가 데이터는 [Google FLEURS](https://huggingface.co/datasets/google/fleurs)의 한국어 validation이며 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)으로 제공됩니다.
- 선택한 원본 float WAV만 임시 16kHz mono PCM16 WAV로 변환하며 캐시 원본은 수정하지 않습니다.
- `.env`, 음성·정답, manifest, 원시 응답과 평가 결과는 Git에서 제외합니다. 로컬 파일의 접근 권한과 삭제 시점은 사용자가 관리해야 합니다.

## 문서와 출처

- [코드 개요](docs/code-overview.md): 구조, 데이터 흐름, 설계 선택
- [API 계약](docs/api-contract.md): 인증, 요청, polling, retry
- [평가 방법](docs/evaluation.md): 데이터 선택, exact CER, 결과와 한계
- API: RTZR [인증](https://developers.rtzr.ai/docs/authentications/), [일반 STT](https://developers.rtzr.ai/docs/stt-file/), [처리량 제한](https://developers.rtzr.ai/docs/en/rate_limit/)
- 데이터: [FLEURS 데이터 카드](https://huggingface.co/datasets/google/fleurs), [논문](https://arxiv.org/abs/2205.12446), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- 주요 도구: [Hugging Face Hub](https://huggingface.co/docs/huggingface_hub/), [SoundFile](https://python-soundfile.readthedocs.io/), [JiWER](https://jitsi.github.io/jiwer/), [uv](https://docs.astral.sh/uv/)
