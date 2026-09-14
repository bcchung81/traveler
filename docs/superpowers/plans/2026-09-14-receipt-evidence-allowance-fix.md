# 일비·식비 로직 점검과 담당자 수정 Implementation Plan

> **For agentic workers:** 서브에이전트 없이 메인 세션에서 인라인 실행. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 출장 기간을 알면서도 일비·식비를 0원으로 두던 문제를 고치고, 담당자가 일비·식비·근무지 내 출장 여비를 일수 또는 금액과 사유로 고칠 수 있게 한다.

**점검 결과(실데이터):** 확정된 출장(2026-07-09_서울_2·_3, 2026-08-27_서울)은 2일×25,000=50,000씩 정상. 확정 전(자동 제안) 출장(2026-07-09_서울 v1)은 기간 7/9~7/10을 알아도 '확인필요 0원(50,000 예정)'. 판정 표는 계산식(25,000×2일)을 숨겨 며칠 기준인지 보이지 않음. 제16조⑤ 단서(항공·수로여행 식비) 미반영. 담당자 판정은 영수증 행만 가능.

**결정(Q&A):**
- 확정 전: **근거가 확실할 때 지급** — 가는 편·오는 편 교통 영수증이 모두 있거나 숙박 체크인·체크아웃이 있으면 제안 기간으로 지급하고 안내. 아니면 지금처럼 확인필요
- 담당자 수정: **인정 일수 또는 금액 + 사유**(일수면 일액×일수, 식사 제공처럼 안 맞으면 금액 직접)

## Global Constraints
- 여행일수 = 시작일~종료일 포함(제16조③⑤). 1박 2일 = 2일
- 기간 근거 신뢰(`period_reliable`): 근무지(모르면 첫 출발지) 기준 나가는 구간과 돌아오는 구간이 모두 있음, 또는 숙박 영수증에 체크인·체크아웃이 모두 있음
- 담당자 정액 판정은 `trip.yaml`의 `allowance_decisions: {일비|식비|근무지 내 출장 여비: {days|amount, reason}}`. 금액이 있으면 금액 우선, 근무지 내 출장 여비는 금액만. 사유 필수
- 판정: 0원=불인정, 규정 계산액보다 적음=감액지급, 같거나 많음=지급(규정 확인필요가 아니고 더 많으면 '규정 한도 초과')
- 새 TripConfig 필드가 기본값이면 fingerprint에서 빼 기존 출장이 새 버전으로 바뀌지 않게 한다
- 제16조⑤ 단서는 안내만(판정 유지): 교통 영수증이 항공뿐이면 식비 행에 확인 권장

## File Structure
```
src/receipt_evidence/models.py        TripConfig.period_reliable·allowance_decisions, ManualDecision.days
src/receipt_evidence/workspace.py     suggest_trip/propose_trip의 period_reliable
src/receipt_evidence/rules.py         allowance_rows(확정 전 신뢰 기간 지급·담당자 정액 판정), decide_all(항공 식비 안내)
src/receipt_evidence/versioning.py    fingerprint에서 기본값 새 필드 제외
src/receipt_evidence/report.py        정액 행 담당자 판정 내역 표기
src/receipt_evidence/web/service.py   save_allowance_decision
src/receipt_evidence/web/routes.py    POST /allowances/{slug}/decision, 표 행에 계산식
src/receipt_evidence/web/templates/review.html  정액 행 판정 바꾸기(일수·금액·사유)·팝업 일수 칸
```

## Tasks
### Task 1: 코어
- [ ] 테스트: period_reliable(왕복·편도·숙박 체크인아웃·폴더 날짜만), 확정 전 신뢰 기간 지급/비신뢰 확인필요, 정액 담당자 판정(일수·금액·0원·초과·사유 없음·근무지 내 일수 거부), 항공 식비 안내, fingerprint 안정성, report 내역
- [ ] 구현 → 통과
### Task 2: 웹
- [ ] 테스트: save_allowance_decision 검증, 판정 표 계산식·정액 행 판정 바꾸기, JSON 오류(팝업), 저장 후 합계
- [ ] 구현 → 통과, 실제 Chrome으로 팝업 확인
### Task 3: 문서·검증
- [ ] README·SKILL·examples, 전체 테스트, 복사본 서버 확인, 서비스 정리
