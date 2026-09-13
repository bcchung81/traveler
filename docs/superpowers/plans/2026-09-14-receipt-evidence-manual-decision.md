# 담당자 판정(인정·감액·불인정) Implementation Plan

> **For agentic workers:** 서브에이전트 없이 메인 세션에서 인라인 실행. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 규정 엔진의 판정을 담당자가 영수증 행마다 인정·감액·불인정으로 바꿀 수 있게 한다. 사유는 필수로 받고, 원래 규정 판정은 함께 남겨 서류에서 추적할 수 있게 한다. 숙박 상한 초과 사유를 넣을 화면이 없던 빈틈도 메운다.

**결정(Q&A):**
- 규정 한도를 넘는 인정도 **허용하되 경고 표시**(화면·서류)
- 조정 범위는 **영수증 행만**(일비·식비·근무지 내 출장 여비는 제외)
- 서류에는 **근거 칸에 '담당자 판정'**을 적고 **'담당자 판정 내역' 절**을 따로 둔다

**Architecture:** `overrides.yaml`의 영수증 항목에 `decision: {verdict, approved_amount, reason}`을 둔다. `rules.apply_manual_decisions`가 규정 판정 뒤에 적용하고 `Decision.manual`에 원래 판정을 보존한다. 파이프라인 `review_receipts`에서 적용하므로 웹 판정 화면·서류·CLI가 같다.

## Global Constraints
- `decision.verdict`는 `지급`·`감액지급`·`불인정`만. 사유 없으면 무효
- 지급은 금액을 저장하지 않고 적용 시점의 청구액 전액. 감액은 0 < 금액 < 청구액
- 형식이 틀린 decision은 규정 판정을 유지하고 사유에 '담당자 판정 형식 오류'
- `over_rule`: 규정 판정이 확인필요가 아니고 담당자 인정액이 규정 인정액보다 클 때
- 담당자 판정이 없으면 서류 출력·fingerprint는 기존과 같다(불필요한 새 버전 없음)
- `save_override`(읽은 값 수정)는 decision을 보존한다

## File Structure
```
src/receipt_evidence/models.py        ManualDecision, Decision.manual
src/receipt_evidence/rules.py         MANUAL_VERDICTS, apply_manual_decisions
src/receipt_evidence/pipeline.py      review_receipts에서 적용
src/receipt_evidence/versioning.py    fingerprint(manual=)
src/receipt_evidence/report.py        근거 표기·담당자 판정 내역 절·한도 초과 안내
src/receipt_evidence/workspace.py     apply_overrides가 decision을 필드로 합치지 않음
src/receipt_evidence/web/service.py   save_decision
src/receipt_evidence/web/routes.py    POST /receipts/{rid}/decision, 판정 화면 edit 파라미터
src/receipt_evidence/web/templates/review.html  판정 바꾸기 details·칩·상한 초과 사유·확인필요에서 바로가기
README.md, SKILL.md, examples/overrides.yaml
```

## Tasks

### Task 1: 코어
- [x] 테스트: 확인필요→지급(청구액, 한도 초과 아님), 숙박 감액→인정(한도 초과), 식비 불인정→지급(한도 초과), 감액 범위·사유 없음·판정 값 오류는 규정 유지+오류 사유, 정액 행 무시
- [x] 테스트: 파이프라인 합계·확인필요 목록·report 내역 절·새 버전, decision 삭제 시 원래 지문, fingerprint는 manual 있을 때만 변화
- [x] 구현 → 통과

### Task 2: 웹
- [x] 테스트: save_decision 검증(영수증 없음·판정 값·사유·감액 범위·규정대로 삭제), save_override가 decision 보존, 판정 화면 영수증 행에만 '판정 바꾸기', 저장 후 담당자·규정 초과 칩과 합계, ?edit 열림, 숙박 상한 초과 사유 입력
- [x] 구현 → 통과

### Task 3: 문서·검증
- [x] SKILL 금지 규칙·README·examples/overrides.yaml
- [x] 전체 테스트, 복사본 서버로 판정 바꾸기→서류 생성→report 확인·스크린샷, 켠 서비스 정리
