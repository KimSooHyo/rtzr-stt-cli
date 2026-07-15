# 철자형 CER 평가

## 목적

Callabo와 같은 한국어 회의 전사 사용 상황을 작게 모사해, 동일한 API 설정에서 문자 전사 오류를 재현 가능하게 확인한다. 이 평가는 제품 전체 품질이나 화자 분리 성능을 주장하기 위한 benchmark가 아니다.

## 표본

- 로컬 KconfSpeech 회의 음성 7개 세션
- 세션당 10개, 총 70개
- 세션별 층: 숫자 포함 이중 전사 2, 숫자 없는 이중 전사 2, 비이중 잡음 표기 2, 일반 발화 4
- 층 내부 순서: SHA-256("20260715:" + 데이터 루트 기준 상대경로) 오름차순
- 최대 총 길이: 900초

실제 manifest, 음원, 정답, 원시 응답과 파일별 가설은 공개 저장소에 포함하지 않는다.

공식 AIHub 페이지의 현재 데이터셋은 [주요 영역별 회의 음성인식 데이터](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=464) 버전 1.5다. 평가에 사용한 로컬 session 메타데이터는 version 1.0을 기록하므로 최신 공개본과 동일하다고 단정하지 않는다.

## API 설정

README의 고정 sommers·ko·GENERAL 설정을 사용한다. 숫자 표기를 정답과 맞추기 위해 ITN을 켜고, 원문 충실도를 위해 간투어 필터는 끈다. 화자 분리, 문단 분리, 단어 timestamp는 이번 지표 범위에서 제외한다.

## 정규화

reference와 hypothesis에 같은 규칙을 순서대로 적용한다.

1. Unicode NFC
2. 이중 전사에서 왼쪽 철자형 선택
3. b/, l/, o/, n/ 잡음 태그 제거
4. 남은 slash, 별표, plus, 괄호 기호만 제거하고 내부 어휘 보존
5. Unicode 문장부호·기호 제거
6. 영문 소문자화
7. 모든 공백 제거
8. 정답이 비면 평가를 중단

숫자는 보존한다. 규칙은 src/rtzr_stt/metrics.py에 한 번만 구현하고 unit test로 고정한다.

## 집계

jiwer.process_characters가 계산한 전체 substitution, deletion, insertion의 합을 전체 reference 문자 수로 나눈 corpus micro CER을 대표값으로 사용한다. 파일별 CER은 오류 사례 탐색에만 사용하며 단순 평균하지 않는다.

## 실행 단계와 사용량

1. 5개 smoke
2. 10개 pilot
3. 70개 final

final 재실행을 포함한 내부 예산은 오디오 60분이다. --resume은 동일 오디오 SHA-256과 동일 canonical config SHA-256을 가진 완료 결과를 재사용한다.

## 결과

2026-07-15 실제 RTZR Batch STT API 실행 결과다.

| 항목 | 값 |
|---|---:|
| 표본 | 70개 |
| 세션 | 7개, 세션당 10개 |
| 총 오디오 | 683.125초 |
| 정답 문자 | 3,966 |
| substitution | 97 |
| deletion | 86 |
| insertion | 76 |
| corpus CER | 6.53% |

5개 smoke와 10개 pilot 후 70개 final을 실행했다. final 완료 뒤 같은 명령을 --resume으로 재실행해 70개 전체가 cache hit이며 추가 전사 요청 없이 같은 corpus CER을 산출하는 것을 확인했다.

## 한계

- 15분 이하의 작은 층화 표본이므로 신뢰구간이나 전체 데이터 대표성을 주장하지 않는다.
- 원본이 짧은 발화 단위여서 장시간 회의의 문맥 유지와 SRT 분할 품질을 충분히 검증하지 않는다.
- CER은 가독성, 의미 보존, 요약 품질을 직접 측정하지 않는다.
- 화자 분리를 사용하지 않아 Callabo의 모든 회의록 기능을 재현하지 않는다.
