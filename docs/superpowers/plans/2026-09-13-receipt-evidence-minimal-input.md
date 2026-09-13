# 첫 화면 입력 최소화·영수증 자동 채움 Implementation Plan

> **For agentic workers:** 서브에이전트 없이 메인 세션에서 인라인 실행. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 새 정산 첫 화면은 영수증 파일(과 출장자)만 받는다. 출장 기간·출장지·경로·근무지는 영수증에서 자동으로 채워 한 번 확인받고, 공용차량·근무지 내 출장은 상황이 될 때만 묻고, 출장 목적·기관·결재선은 서류 만들기 직전에 받는다.

**결정(Q&A):**
- 출장 폴더 이름: 임시 폴더에 올리고 **읽은 뒤 자동으로** `YYYY-MM-DD_출장지`로 바꾼다
- 파일을 놓으면 **바로 읽기 시작**
- 출장 목적: **선택 입력**, 비면 서류에 `-`와 안내
- 새 출장자 여비 구분: **제2호 기본**, 확인 카드에서 바꿀 수 있음

**Architecture:** 코어에 `stations.py`(역·공항 → 도시), `workspace.suggest_trip`(필드별 자동값+근거), 임시 폴더·`move_trip`(경로 치환 이동)·`renames.json`을 더한다. `propose_trip`이 같은 제안을 써서 CLI도 같아진다. 웹은 첫 화면·확인 카드·상황별 질문·서류 정보 패널로 재배치한다.

## Global Constraints
- 영수증 `region`(가맹점 주소)은 출장지 근거로 쓰지 않는다(실데이터: 코레일 본사 대전, 결제대행사 서울 강남)
- 결제일(paid_at)은 기간 근거로 쓰지 않는다(사전 예매)
- 폴더명이 `YYYY-MM-DD_출장지` 형식이면 사용자가 정한 것으로 보고 출장지를 그대로 쓴다
- 폴더 이름 변경은 문서(latest.json)가 없을 때만, 임시 폴더이거나 자동으로 이름 붙인 폴더일 때만
- VLM 프롬프트·캐시 키는 바꾸지 않는다(기존 추출 결과 재사용)
- 상황별 질문은 판정을 막지 않는다. trip.yaml에 해당 키가 생기면 다시 묻지 않는다
- 기존 계약 유지: CLI 폴더 계약, `/info`·`/resolve`·`/finalize` 경로, 템플릿 문구 계약(“영수증 읽기 시작” 등은 편집 화면에 유지)

## File Structure
```
src/receipt_evidence/stations.py              (새) 역·공항 이름 → 도시, place_of()
src/receipt_evidence/workspace.py             Suggestion, suggest_trip, propose_trip 개선, STAGING_PREFIX, staging_trip_id, is_staging, unique_trip_id, move_trip, renames
src/receipt_evidence/web/service.py           travelers, create_staging_trip, ensure_traveler(제2호), trip_suggestion, confirm_trip(재이름), moved_to, TripSummary.display_name·staging
src/receipt_evidence/web/jobs.py              alias
src/receipt_evidence/web/actions.py           do_extract 끝에 자동 이름 변경
src/receipt_evidence/web/app.py               TripMoved → 303/307/HX-Redirect
src/receipt_evidence/web/routes.py            /new 최소화, /files 자동 읽기, /trip 확정, /docinfo, /resolve 확장
src/receipt_evidence/web/templates/*          upload(첫 화면·편집), extract(출장 정보 카드), review(질문·서류 정보), home(임시 폴더 표시), _steps(compact), _local_note(한 줄), _trip_fields 삭제
tests/…                                       helpers.new_trip 새 흐름, 코어·웹 테스트 추가, 통합 웹 E2E 갱신
README.md, SKILL.md                           웹 흐름 문구
```

## Tasks

### Task 1: 코어 자동 채움·폴더 이동
- [ ] 테스트: 역 이름 정규화(용산역·KTX 표기·괄호) → 도시, 표에 없으면 None
- [ ] 테스트: suggest_trip — 왕복(나주↔용산 → 서울·근무지 나주), 돌아오는 표만 있고 근무지 알 때, 표에 없는 역, 숙박 체크인·아웃 기간, 결제일·영수증 region 무시, 폴더명 우선, 숙박 지역 추정(guessed), 결제자명
- [ ] 테스트: move_trip — data·out 이동과 work/*.json 경로 치환, 문서가 있으면 거부, unique_trip_id(_2), renames 따라가기
- [ ] 구현 → 통과

### Task 2: 서비스·작업·주소 이동
- [ ] 테스트: 임시 출장 생성(새 출장자 제2호 기본), 읽기 후 자동 이름 변경·작업 별칭, 옛 주소 303/HX-Redirect, 확정 시 trip.yaml·traveler.yaml 저장과 재이름
- [ ] 구현 → 통과

### Task 3: 화면
- [ ] 테스트: 첫 화면에 출장 정보 입력칸 없음·출장자 칩·한 줄 단계, 파일 올리면 읽기 화면으로, 출장 정보 카드 자동값·근거·CTA 한 번으로 확정, 공용차량·근무지 내 질문, 서류 정보 패널(목적 비면 안내)과 서류 만들기, 홈 임시 폴더 표시
- [ ] 구현 → 통과, 기존 웹 테스트를 새 흐름으로 갱신

### Task 4: 문서·검증
- [ ] README·SKILL 웹 흐름, 통합 웹 E2E 새 흐름으로 갱신
- [ ] 전체 단위·통합 테스트, 실제 웹 서버 화면 확인, 켠 서비스 정리
