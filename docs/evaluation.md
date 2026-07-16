# FLEURS STT 실행과 exact CER 평가 방법

## 목적과 데이터

이 흐름은 고정한 FLEURS 한국어 읽기 음성으로 RTZR Batch STT API를 바로 시험하고, 같은 입력으로 exact CER까지 반복 가능하게 계산한다. 입력 음성과 정답은 모두 같은 [Google FLEURS 데이터셋](https://huggingface.co/datasets/google/fleurs)에서 자동으로 가져오며 사용자가 별도 평가 데이터를 준비하지 않는다.

- repository: `google/fleurs`
- revision: `70bb2e84b976b7e960aa89f1c648e09c59f894dd`
- language: `ko_kr`
- validation metadata: `data/ko_kr/dev.tsv`
- audio archive: `data/ko_kr/audio/dev.tar.gz`
- license: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)

FLEURS의 출처와 license를 결과 manifest에 기록한다. 원본은 변경하지 않고, 선택된 float WAV만 RTZR 업로드를 위해 16kHz mono PCM16 WAV로 변환한다. 이 변환 사실도 공개 문서에 표시한다.

데이터셋을 인용할 때는 FLEURS 데이터 카드가 안내하는 [원 논문](https://arxiv.org/abs/2205.12446)도 함께 참고한다.

## 표본 고정과 다운로드

`evaluate --samples N`은 결과를 보기 전에 `dev.tsv`의 앞 N행을 고른다. 기본값 1은 실제 STT smoke test용이며, 결과를 집계할 때는 50개를 명시해 사용한다. 50개는 657.12초다. 난수나 이전 결과를 사용하지 않으므로 같은 revision과 N에서는 표본이 같다.

`hf_hub_download()`는 metadata를 먼저 로컬 캐시에 확보한다. 프로그램은 전체 TSV의 형식, 문장 ID, 비어 있지 않은 `transcription`, frame 수, 중복 표본 ID와 선택 표본의 총 길이를 검사한다. 원본 파일명은 경로를 포함하지 않는 `<숫자>.wav` 형식이어야 하며, 숫자 stem을 표본 ID와 결과 디렉터리 이름으로 사용한다. 요청 수가 전체 행보다 많거나 선택 길이가 고정된 900초 제한을 넘으면 오디오를 내려받거나 API client를 만들기 전에 종료한다.

그 다음 오디오 archive를 캐시하고 다음을 모두 확인한다.

- 절대 경로와 `..` 경로가 없는 안전한 tar member
- 중복 member가 없고 regular file이 `dev/*.wav` 형식인지 여부
- 선택한 모든 WAV가 존재하는지 여부
- 원본이 float WAV이며 16kHz, mono, metadata와 같은 frame 수이고 유한한 비무음 sample을 갖는지 여부
- 변환 결과가 16kHz, mono, PCM16이고 길이가 같으며 비무음인지 여부

선택 표본마다 FLEURS 문장 ID, 원본 파일명, 정답, 길이와 원본 WAV·업로드 WAV·정답 SHA-256을 보존한다. 모든 표본의 준비가 끝난 뒤에만 credential을 읽고 RTZR client를 만든다. 임시 PCM 파일은 실행 종료 시 삭제하고 Hugging Face 캐시 원본은 유지한다.

## 고정 API 설정

`src/rtzr_stt/config.py`의 설정을 모든 표본에 동일하게 적용한다.

- `sommers`, 한국어, `GENERAL`
- ITN 사용
- 간투어 필터 미사용
- 화자 분리, 문단 분리, 비속어 필터, 단어 timestamp 미사용

설정을 바꾸면 이전 결과와 직접 비교하지 않고 별도 실행으로 취급한다. `summary.json`의 config SHA-256으로 설정 동일성을 확인할 수 있다.

## Exact CER

reference는 FLEURS가 ASR용으로 제공하는 `transcription`이며, 별도 필드인 `raw_transcription`은 사용하지 않는다. hypothesis는 완료 응답에서 각 utterance message의 앞뒤 공백을 제거하고, 비어 있지 않은 message를 순서대로 단일 공백으로 연결한 문자열이다. 두 문자열을 만든 이후에는 애플리케이션의 추가 정규화를 적용하지 않는다.

JiWER에는 앞뒤 공백을 제거하는 기본 CER 변환 대신 문자 배열 변환만 명시한다. 따라서 비교 함수에 전달된 문자열의 첫 문자와 마지막 문자가 공백이어도 오류 계산에 포함된다. 단건 `transcribe --reference`는 파일 표현을 위해 정답을 읽을 때만 앞뒤 공백을 제거한다.

따라서 다음 차이는 모두 실제 오류다.

- 내부 공백
- 문장부호와 기호
- 영문 대소문자
- Unicode 표현 차이

JiWER `process_characters`로 substitution, deletion, insertion을 계산한다.

~~~text
CER = (substitutions + deletions + insertions) / reference characters
~~~

대표값은 파일별 CER 평균이 아니라 모든 표본의 오류 수와 정답 문자 수를 합산한 corpus micro CER이다.

## 실행과 결과

실제 STT 연결을 한 건으로 먼저 확인한다.

~~~bash
uv run --locked rtzr-stt evaluate \
  --output-dir outputs/fleurs-smoke
~~~

인증, multipart 전사 생성, polling, JSON·TXT·SRT 저장과 CER 계산이 모두 실행된다. 이를 확인한 뒤 50건으로 같은 흐름을 확장한다.

~~~bash
uv run --locked rtzr-stt evaluate \
  --samples 50 \
  --output-dir results/fleurs
~~~

루트에는 자동 `manifest.json`과 `summary.json`이 생긴다. 각 표본 디렉터리에는 `response.json`, `transcript.txt`, `transcript.srt`, `metrics.json`이 생긴다.

- `manifest.json`: dataset provenance, 앞 N행 선택 규칙, 표본 ID, 원본 파일명, 정답, 길이와 hash
- `summary.json`: dataset provenance, 선택 규칙, 표본 수·총 길이, manifest/config hash, corpus 오류 수·CER, 표본별 오류 수

출력 경로는 존재하지 않거나 비어 있어야 한다. 다른 실행의 파일이 섞이지 않도록 재실행에는 새 디렉터리를 사용한다.

실제 음원, 자동 manifest, 원시 응답, 정답·가설과 파일별 결과는 공개 저장소에 넣지 않는다. 공개 보고에는 실행일, 표본 수·총 길이, dataset revision, manifest/config hash와 aggregate 오류 수·CER만 기록한다.

## 실제 실행 확인

2026-07-16에 고정 revision의 한국어 validation 앞 50개를 현재 평가 흐름으로 순차 실행했다.

| 항목 | 결과 |
|---|---:|
| 표본 수·총 길이 | 50개·657.12초 |
| 정답 문자 수 | 3,194 |
| substitution·deletion·insertion | 70·72·137 |
| 전체 오류 수 | 279 |
| corpus micro CER | 약 8.74% |

이 수치는 다운로드, PCM16 변환, RTZR 호출, 결과 저장과 집계가 끝까지 연결되는지 확인한 기록이다. 읽기 음성 50개에 대한 값이므로 제품 정확도나 회의 음성 성능으로 일반화하지 않는다.

재현 조건과 저장 결과를 대조하기 위해 다음 SHA-256도 함께 확인했다.

- metadata: `6b236de107c6a1672233f6d710d26adfdb55570a3e6e35aca9dc4ff2be01cea4`
- manifest: `f0ec63196a107194c1475e43b076cc885a75d40288e929f80313e11792b57f4a`
- 고정 API config: `a74691f4d02c973986c022804e03b69c0345ab3ed130b4e13e9b35c3dabd355d`
- summary 파일: `90cdcde6310251121c3099073d622f325725ebf17511cfb7d1711eaf0ce93d3d`

## 실패와 재실행

평가는 stateless하며 표본을 순차 호출한다. 중단 작업 재개, 완료 cache, 동시 실행은 지원하지 않는다. 중간 실패 시 이미 완료된 표본 산출물이 남을 수 있지만 자동 복구 입력으로 사용하지 않는다. 실패한 출력 경로는 더 이상 비어 있지 않아 재사용할 수 없다. 새 경로 또는 빈 디렉터리에서 전체 명령을 다시 실행하면 완료했던 표본도 새 API 작업으로 생성되므로 비용과 사용량을 먼저 확인해야 한다.

## 한계

- FLEURS 한국어는 일반 읽기 음성이며 회의·자연 대화나 장시간 문맥을 대표하지 않는다.
- TSV 앞 N행을 고정한 표본이며 무작위·균형 표집이 아니다. 보고한 50개에는 고유 문장 29개의 여러 녹음이 포함된다.
- 화자 분리와 회의록 제품의 전체 기능을 평가하지 않는다.
- exact CER은 비교 규칙이 단순하고 투명하지만 의미 보존, 가독성, 자막 분할 품질을 직접 측정하지 않는다.
- RTZR 설정에서 ITN을 사용하므로 띄어쓰기·문장부호·숫자 표기 차이까지 포함한 값이며 순수 음향 인식 오류율이 아니다.
- 띄어쓰기와 문장부호를 포함하므로 다른 정규화 CER 결과와 직접 비교할 수 없다.
- dataset revision과 요청 설정을 고정해도 서버 모델이 갱신되면 결과가 달라질 수 있다.
- 출력은 자동 암호화·익명화되지 않는다.
