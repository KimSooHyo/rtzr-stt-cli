# RTZR STT CLI

RTZR Batch STT API로 오디오를 TXT·SRT로 저장하고, 선택적으로 철자형 CER을 계산하는 작은 Python CLI입니다.

## 1분 Quickstart

요구 사항은 Python 3.10과 [uv](https://docs.astral.sh/uv/)입니다.

~~~bash
git clone YOUR_REPOSITORY_URL
cd rtzr-stt-cli
uv sync --frozen
cp .env.example .env
~~~

.env에 개발자 콘솔에서 발급받은 값을 입력합니다.

~~~dotenv
STT_CLIENT_ID=your-client-id
STT_CLIENT_SECRET=your-client-secret
~~~

WAV 파일을 TXT와 SRT로 전사합니다.

~~~bash
uv run rtzr-stt transcribe sample.wav \
  --format all \
  --output-dir outputs/sample
~~~

생성 파일은 response.json, transcript.txt, transcript.srt입니다. 원시 응답도 함께 저장하므로 서버 측 결과 보관 기간과 무관하게 실행 결과를 다시 확인할 수 있습니다.

## 로컬 credential 파일 사용

credential을 공개 저장소 바깥에 두었다면 전역 옵션을 서브 명령 앞에 지정합니다.

~~~bash
uv run rtzr-stt --env-file ../.env transcribe sample.wav \
  --format all \
  --output-dir outputs/sample
~~~

환경 변수 STT_CLIENT_ID와 STT_CLIENT_SECRET이 설정되어 있으면 해당 값이 dotenv 파일보다 우선합니다. credential과 access token은 출력하지 않습니다.

## 방금 전사한 파일의 CER 측정

UTF-8 정답 텍스트를 함께 지정하면 같은 실행에서 철자형 CER을 계산합니다.

~~~bash
uv run rtzr-stt transcribe sample.wav \
  --format all \
  --output-dir outputs/sample \
  --reference sample.txt
~~~

콘솔에는 CER 백분율이 표시되고 metrics.json이 추가됩니다. CER은 낮을수록 좋으며, 정확도 백분율과 같은 의미가 아닙니다.

## manifest 일괄 평가

data/manifest.example.csv를 복사해 경로를 채웁니다. 오디오와 정답 경로는 manifest 파일의 위치를 기준으로 해석합니다.

~~~csv
sample_id,audio_path,reference_path,session_id,stratum
sample-001,audio/sample.wav,references/sample.txt,session-demo,clean
~~~

~~~bash
uv run rtzr-stt evaluate manifest.csv \
  --output-dir results/evaluation \
  --max-audio-minutes 15 \
  --resume
~~~

네트워크 요청 전에 파일 존재 여부, ID 중복, 정규화 후 빈 정답, 총 오디오 길이를 검사합니다. 제한을 초과하면 API를 호출하지 않습니다. --resume은 오디오 해시와 고정 설정 해시가 모두 같은 완료 결과만 재사용합니다.

자세한 표본·정규화·집계 규칙은 [평가 문서](docs/evaluation.md)를 참고하세요.

## 고정 API 설정

평가에서는 비교 가능성을 위해 다음 설정을 변경하지 않습니다.

~~~json
{
  "model_name": "sommers",
  "language": "ko",
  "domain": "GENERAL",
  "use_diarization": false,
  "use_itn": true,
  "use_disfluency_filter": false,
  "use_profanity_filter": false,
  "use_paragraph_splitter": false,
  "use_word_timestamp": false,
  "keywords": []
}
~~~

요청과 테스트의 공식 문서 대응은 [API 계약 문서](docs/api-contract.md)에 정리했습니다.

## 테스트

~~~bash
make check
~~~

이 명령은 정적 검사, 포맷 검사, unit test와 HTTP contract test를 실행하며 실제 API는 호출하지 않습니다.

실제 API 한 건은 명시적으로만 실행합니다.

~~~bash
make smoke-test SMOKE_AUDIO=/path/to/short.wav
~~~

## 오류 해결

- credential 오류: .env의 두 항목이 비어 있지 않은지, --env-file이 서브 명령보다 앞에 있는지 확인합니다.
- HTTP 401 또는 H0002: 발급 정보를 다시 확인합니다. 조회 중 401은 한 번만 재인증합니다.
- HTTP 429 또는 A0003: 조회 요청은 1·2·4초 간격으로 최대 세 번 재시도합니다. 기본 polling 간격은 공식 권장값인 5초입니다.
- 시간 초과: 기본 제한은 1,800초입니다. 이미 생성된 작업의 중복 가능성 때문에 모호하게 실패한 생성 POST는 자동 재전송하지 않습니다.
- H0010: 공식 지원 형식(mp4, m4a, mp3, amr, flac, wav)인지 확인합니다.

## 데이터와 출처

CER 평가는 AIHub의 [주요 영역별 회의 음성인식 데이터](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=464)에서 내려받은 로컬 사본을 사용했습니다.

- 평가 범위: 7개 세션에서 층화 선택한 70개 발화
- 총 오디오 길이: 683.125초
- 데이터 유형: 한국어 회의·방송 발화 WAV와 전사 라벨
- 로컬 메타데이터 버전: 1.0
- AIHub 공개 페이지의 현재 버전: 1.5

로컬 사본과 현재 공개 버전이 동일하다고 단정하지 않으며, 이 버전 차이를 평가 결과의 한계로 기록했습니다. 표본 선정과 정규화, aggregate CER은 [평가 문서](docs/evaluation.md)에서 확인할 수 있습니다.

저작권·이용 조건과 개인정보 보호를 위해 평가 음원, 원문 라벨, 실제 manifest와 파일별 전사 결과는 이 저장소에 포함하지 않습니다. 사용자는 AIHub의 이용 절차를 따르거나 자신이 이용 권한을 가진 데이터를 로컬 manifest에 연결해야 합니다.

구현과 평가 규칙의 근거로 다음 공식 문서를 참고했습니다.

- [RTZR 인증 가이드](https://developers.rtzr.ai/docs/authentications/)
- [RTZR 일반 STT](https://developers.rtzr.ai/docs/stt-file/)
- [RTZR 처리량 제한](https://developers.rtzr.ai/docs/en/rate_limit/)
- [AIHub 전사 규칙](https://aihub.or.kr/aihubnews/notice/view.do?currMenu=132&nttSn=9746&pageIndex=1&topMenu=103)

## 한계

- 파일 기반 Batch STT 전용이며 Streaming STT와 웹 UI를 제공하지 않습니다.
- 화자 분리는 끈 상태이므로 화자 분리 품질을 평가하지 않습니다.
- CER은 문자 단위 오류율로 의미 보존이나 회의록 가독성을 직접 측정하지 않습니다.
- 작은 층화 표본 결과는 전체 데이터나 실제 회의 서비스 성능으로 일반화할 수 없습니다.
