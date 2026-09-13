---
name: receipt-evidence
description: data/<출장자>/<출장>/ 폴더의 영수증을 로컬 Qwen3-VL로 읽고 공무원 여비 규정(korean-law)으로 판정해 출장별 kordoc HWPX 증빙내역서를 만든다. 여러 출장자·여러 출장 일괄, 추가 제출(새 버전)을 지원. "영수증 정리해줘", "출장비 증빙 hwpx", "여비 정산 서류", "영수증 추가했어" 요청에 사용.
---

# 출장여비 영수증 증빙서류 자동화

## 폴더 계약
- 영수증은 `data/<출장자>/<출장>/`에 넣는다. 출장 폴더 이름은 `YYYY-MM-DD_출장지` (예: `data/정백철/2026-07-09_서울/`).
- `data/<출장자>/traveler.yaml`: 여비 구분(grade)·근무지·결재선. 없으면 철도·숙박은 확인필요.
- `data/<출장자>/<출장>/trip.yaml`: 출장기간·출장지·목적·경로. 없으면 영수증으로 자동 제안하고 일비·식비는 확인필요.
- `data/<출장자>/<출장>/overrides.yaml`: 영수증에 없는 사실이나 오인식을 사용자 확인값으로 교정(`receipt_id`별 필드, `clear_warnings`).
- 예시는 `examples/`에 있다.

## 웹앱으로 하기
- `uv run receipt-evidence web` 실행 후 http://127.0.0.1:8765 를 연다(이 컴퓨터에서만 열림). 홈 → 새 정산 → 올리기 → 읽은 값 확인 → 판정 검토 → 서류 완성 순서.
- 로컬 AI(llama-server)는 새 영수증을 읽을 때만 자동으로 켜지고 다 읽으면 꺼진다. 미리 켜 둘 필요 없다.

## 절차
1. VLM은 미리 켜지 않는다: `run`이 새 영수증을 읽어야 할 때만 `scripts/start_vlm.sh`로 켜고 끝나면 끈다. 영수증이 많으면 직접 `VLM_PARALLEL=4 bash scripts/start_vlm.sh &`로 켜고 `--workers 4`를 쓴 뒤, 끝나면 그 서버를 꼭 끈다.
2. 영수증이 `data/` 루트나 출장자 폴더에 바로 있으면 어느 출장자·출장인지 **사용자에게 묻고 확인을 받은 뒤** 폴더로 옮긴다. 임의로 옮기지 않는다.
3. `traveler.yaml`이 없으면 사용자에게 여비 구분(제1호/제2호)·근무지·결재선을 묻고 `examples/traveler.yaml`을 복사해 채운다. 답이 없으면 비워 둔다.
4. 실행: `uv run receipt-evidence run` (특정 대상만: `--traveler 정백철 --trip 2026-07-09_서울`).
5. 출력의 출장별 줄(버전·인정액·확인필요)과 `out/summary-<run_id>.md`를 사용자에게 보여 준다. 확인필요 항목은 그대로 전달하고 답을 받는다.
   - 출장기간·지역·사유 → `trip.yaml` (자동 제안된 경우 `out/<출장자>/<출장>/work/trip.proposed.yaml`을 보여 주고 확인받아 `trip.yaml`로 저장)
   - 실제 숙박일·지역, 오인식 값 → `overrides.yaml` (receipt_id는 `out/<출장자>/<출장>/work/receipts.json`)
6. 같은 명령으로 다시 실행한다. 이미 읽은 영수증은 캐시를 쓰고, 달라진 출장만 새 버전(v2, v3…)과 `changes.md`가 생긴다. 영수증을 추가 제출할 때도 파일을 출장 폴더에 넣고 다시 실행하면 된다.
7. 최신 문서 `out/<출장자>/<출장>/v<N>/evidence.hwpx`와 합계 검증(`verify.json`) 결과를 보고한다. exit 3(합계 불일치)이면 report.md와 hwpx 표를 대조해 원인을 설명한다. exit 2면 요약의 오류 행을 보여 준다.

## 금지
- 영수증 이미지·전사문·카드번호를 대화나 외부 도구로 보내지 않는다. 법령 조회에는 법령명과 조문 번호만 쓴다.
- 확인필요 항목을 임의로 지급으로 바꾸지 않는다. 사용자 확인값만 `overrides.yaml`·`trip.yaml`에 적는다.
- 사용자 확인 없이 `data/` 안의 파일을 옮기거나 지우지 않는다.

## 산출물
- `out/<출장자>/<출장>/latest.json` — 최신 버전 번호
- `out/<출장자>/<출장>/v<N>/` — evidence.hwpx, report.md, decisions.json, verify.json, changes.md(v2부터), attachments/
- `out/summary-<run_id>.md` / `.json` — 일괄 요약
