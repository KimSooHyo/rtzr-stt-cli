# 철자형 CER 평가 방법

## 목적

이 평가는 RTZR Batch STT API 연동과 지표 계산 과정이 같은 입력에서 반복 가능한지 확인하기 위한 가벼운 검증이다. 특정 데이터셋이나 제품 전체의 정확도, 화자 분리 성능을 주장하는 benchmark가 아니다.

## 데이터 준비와 권한

평가에는 다음 조건을 모두 만족하는 WAV와 UTF-8 정답만 사용한다.

- 음원과 정답을 평가에 사용할 권한이 있다.
- 외부 STT API에 음원을 전송해 처리할 수 있다.
- 개인정보 포함 여부를 확인했고 필요한 보호 조치를 했다.

해당 자료는 과학기술정보통신부·한국지능정보사회진흥원의 AI Hub 사업결과물이다. [데이터셋 페이지](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=464)와 [이용정책](https://aihub.or.kr/intrcn/guid/usagepolicy.do?currMenu=151&topMenu=105)을 함께 확인해야 한다. 이 프로젝트에서는 AIHub 원음을 외부 API에 다시 업로드할 수 있다는 별도 허가를 확인하지 못했다. 따라서 과거 비공개 실행 수치를 현재 코드의 재현 결과로 제시하지 않는다.

## Manifest와 표본 고정

`evaluate`는 표본을 고르지 않는다. 사용자가 선택한 표본을 CSV manifest로 고정하고 위에서 아래 순서대로 처리한다.

~~~csv
sample_id,audio_path,reference_path,session_id,stratum
sample-001,audio/sample.wav,references/sample.txt,session-demo,clean
~~~

- 오디오와 정답 경로는 manifest 파일 위치를 기준으로 해석한다.
- `sample_id`는 영문, 숫자, `.`, `_`, `-`만 사용하고 중복하지 않는다.
- `session_id`는 같은 녹음 단위, `stratum`은 비교할 조건을 나타낸다.
- 표본을 추출한다면 결과를 보기 전에 규칙과 난수를 고정하고, 세션이 한쪽 조건에 치우치지 않는지 기록한다.

실제 음원, 정답, manifest, 원시 응답과 파일별 가설은 공개 저장소에 넣지 않는다. `data/manifest.example.csv`는 형식만 제공한다.

## API 호출 전 검증과 입력 기록

`evaluate`는 credential을 읽고 API client를 만들기 전에 manifest 전체를 검사한다.

- 필수 열, 중복되거나 안전하지 않은 `sample_id`
- 오디오·정답 파일 존재 여부와 UTF-8 정답
- 정규화 후 비어 있지 않은 정답
- WAV 헤더와 실제 프레임 데이터 일치, 0초보다 긴 오디오
- 전체 오디오 길이가 `--max-audio-minutes` 이내인지 여부

기본 길이 제한은 15분이다. `summary.json`에는 manifest와 고정 API 설정의 SHA-256, 각 오디오·정답의 SHA-256을 기록한다. 이 값은 입력과 설정이 같은지 확인하기 위한 것이며, 원본을 대신하거나 서버 모델 버전을 고정하지는 않는다.

## 고정 API 설정

`src/rtzr_stt/config.py`의 다음 설정을 모든 표본에 동일하게 적용한다.

- `sommers`, 한국어, `GENERAL`
- 숫자 표기를 정답과 맞추기 위해 ITN 사용
- 원문 철자형 비교를 위해 간투어 필터 미사용
- 화자 분리, 문단 분리, 비속어 필터, 단어 timestamp 미사용

설정을 바꾸면 이전 결과와 직접 비교하지 않고 별도 실행으로 취급한다.

## 정규화

reference와 hypothesis에 같은 규칙을 순서대로 적용한다. 규칙은 `metrics.py` 한 곳에 구현하고 unit test로 고정한다.

1. Unicode NFC
2. 이중 전사에서 왼쪽 철자형 선택
3. `b/`, `l/`, `o/`, `n/` 잡음 태그 제거
4. 남은 slash, 별표, plus, 괄호 기호만 제거하고 내부 어휘 보존
5. Unicode 문장부호·기호 제거
6. 영문 소문자화
7. 모든 공백 제거
8. 정답이 비면 평가 중단

숫자는 보존한다. 한국어 음성 라벨을 사용할 때는 AIHub의 [한국어 음성 전사 규칙 v1.0](https://aihub.or.kr/aihubnews/notice/view.do?currMenu=132&nttSn=9746&pageIndex=1&topMenu=103)과 해당 데이터셋의 실제 라벨 형식을 함께 확인한다.

## CER 집계와 해석

JiWER `process_characters`로 substitution, deletion, insertion을 구한다.

~~~text
CER = (substitutions + deletions + insertions) / reference characters
~~~

대표값은 파일별 CER의 평균이 아니라 모든 표본의 오류 수와 정답 문자 수를 먼저 합산한 corpus micro CER이다. 층별 CER도 같은 방식으로 계산한다. 파일별 CER과 층별 차이는 오류 사례를 찾는 신호로만 사용하고, 표본 수와 조건을 통제하지 않은 채 원인이나 전체 성능으로 일반화하지 않는다.

## 실행과 결과 확인

~~~bash
uv run --locked rtzr-stt evaluate data/manifest.csv \
  --output-dir results/evaluation \
  --max-audio-minutes 15
~~~

각 표본 디렉터리에는 `response.json`, `transcript.txt`, `transcript.srt`, `metrics.json`이 생긴다. 루트 `summary.json`에는 표본 수, 총 길이, 입력·설정 hash, corpus CER, 층별·파일별 지표가 저장된다.

재현 결과를 보고할 때는 코드 commit, 실행일, 표본 선택 규칙, 표본 수·총 길이, manifest·설정 hash, 오류 수와 corpus CER을 함께 기록한다. 서버 모델이 갱신되면 입력과 설정이 같아도 결과가 달라질 수 있다.

## 한계

- CER은 의미 보존, 가독성, 자막 분할 품질을 직접 측정하지 않는다.
- 짧은 표본은 장시간 회의 문맥이나 전체 데이터 분포를 대표하지 않는다.
- 화자 분리를 사용하지 않으므로 회의록 제품의 모든 기능을 평가하지 않는다.
- 중단 작업 재개, 결과 cache, 같은 출력 디렉터리의 동시 실행은 지원하지 않는다.
- 입력 snapshot을 만들지 않아 검증 뒤 업로드 전에 파일이 바뀌는 경우까지 방어하지 않는다.
- 출력에는 발화와 정규화된 정답이 포함될 수 있으며 자동 암호화·익명화를 제공하지 않는다.
