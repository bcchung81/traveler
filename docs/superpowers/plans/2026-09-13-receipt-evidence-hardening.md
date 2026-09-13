# 오프라인 사용·최신 규정·오류 개선 Implementation Plan

> **For agentic workers:** 사용자는 서브에이전트 없이 메인 세션에서 직접 실행하기를 원한다 → 인라인 실행. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 프로덕션 분석에서 나온 오류와 제안을 반영한다. 인터넷이 없어도 끝까지 정산할 수 있게 하고, korean-law MCP로 받는 최신 규정을 더 정확히 쓰며(개정 감지·출장일 기준·조문 금액), 판정·추출·웹의 오류를 고친다.

**결정(Q&A):** 출장일에 시행되던 규정 금액표를 갖고 있지 않으면 현행 규정으로 판정하고 **안내만** 붙인다(판정은 바꾸지 않음).

**Architecture:** 코어에 `LawBook`(스냅샷 보관소·오프라인 폴백·출장일 선택·개정 기록)과 `LawParams`(조문 금액 파싱), `HashCache`·`out_lock`·`NotFound`(workspace)를 더한다. MCP 호출기는 첫 호출 때 기동(lazy)해 추출 작업이 법령·kordoc 서버에 묶이지 않게 한다. 사용자 확인값 합성은 `compose_receipts` 한 곳에서 한다(웹·CLI 공용).

## Global Constraints
- 기존 계획의 제약 유지(루프백·같은 출처·NFC·파일당 30MB·작업 스레드 1개·템플릿 문구 계약)
- 법령 조회 실패는 **보유한 가장 최신 스냅샷**으로 계속한다. 보유본이 하나도 없을 때만 오류. 패키지에 기준본(MST 287535) 동봉
- 오프라인 폴백은 fingerprint에 영향 없음(법령 MST·시행일만 반영)
- fingerprint에 `REPORT_VERSION` 추가 → 기존 출장은 다음 서류 만들기 때 새 버전이 한 번 생긴다
- npx 패키지 버전 고정: `korean-law-mcp@4.13.0`, `kordoc@4.13.1`, `--prefer-offline`. 환경변수 `KOREAN_LAW_MCP`, `KORDOC_MCP`, `LAW_OC`로 덮어쓰기
- 새 의존성: `pillow-heif`(HEIC). 글꼴은 한글 상용 2,350자+ASCII 서브셋 woff2를 `static/fonts/`에 동봉(OFL 라이선스 파일 포함), CDN 링크 제거

## File Structure
```
src/receipt_evidence/workspace.py     NotFound, HashCache, out_lock(BusyError), compose_receipts/clear_warnings, trip_file_owners
src/receipt_evidence/law.py           LawParams 파싱, 스냅샷 보관소, LawBook(get_law_book/load_law_book), 개정 기록
src/receipt_evidence/law_baseline/287535.json  (새) 기준 스냅샷
src/receipt_evidence/models.py        LawParams, LawSnapshot.params, ReceiptImage.orientation, PipelineResult.law_notes
src/receipt_evidence/mcp_client.py    lazy 기동, 버전 고정·환경변수
src/receipt_evidence/rules.py         region_key(통합특별시·미확정), 숙박 박수, 조문 금액 사용, 제16조② 안내
src/receipt_evidence/validate.py      멱등 재검증, 금액·승인번호 대조 강화
src/receipt_evidence/vlm.py           전송오류·5xx 재시도
src/receipt_evidence/extract.py       영수증 1장 실패 격리, 회전 이미지 캐시 키
src/receipt_evidence/ingest.py        EXIF 회전, HEIC, 이전 manifest 재사용(INGEST_VERSION)
src/receipt_evidence/pipeline.py      법령 먼저·출장별 격리·data 전체 중복·잠금·법령 안내
src/receipt_evidence/report.py        REPORT_VERSION, 조문 금액·규정 출처 안내
src/receipt_evidence/versioning.py    fingerprint에 REPORT_VERSION
src/receipt_evidence/cli.py           개정·오프라인 안내 출력, BusyError
src/receipt_evidence/web/*            LawBook 사용, stale 차단, 오류 화면, 404 예외 정리, 개정 배너, 해시 캐시, 글꼴
run-app.sh, README.md, SKILL.md       오프라인 동작·환경변수 문서
```

## Tasks

### Task 1: workspace 공용(NotFound·HashCache·잠금·합성)
- [x] 테스트: HashCache가 (크기, mtime) 같으면 재계산하지 않음 / out_lock 중첩 획득 시 BusyError / compose_receipts가 수정된 금액을 재검증하고 clear_warnings를 마지막에 적용 / trip_file_owners가 data 전체 출장의 파일 해시를 모음
- [x] 구현 → 통과

### Task 2: 법령(조문 금액·보관소·폴백·출장일·개정)
- [x] 테스트: 제18조·제16조 본문에서 2만/1만/감액 1만/4시간/10분의 3/2분의 1 파싱, 문구가 달라지면 해당 값 None
- [x] 테스트: 조회 성공 → snapshots/<mst>.json·오늘 표식 저장 / 조회 실패 → 최신 보유본 + offline 안내 + 10분 내 재조회 안 함 / 보유본 없음 → 기준본 / MST 변경 → amendments.json 기록
- [x] 테스트: for_date(출장 시작일) — 시행일이 출장일 이전인 스냅샷 중 최신, 없으면 현행+안내
- [x] 테스트: MCP lazy 기동(with 진입만으로는 프로세스 없음), 버전 고정 인자·환경변수
- [x] 구현 → 통과

### Task 3: 판정
- [x] 테스트: 통합특별시 — 종전 전남 시·군(예: 목포) → 그 밖의 지역, 광주 자치구(광산구) → 광역시, 판별 불가 → 확인필요(비고 5), trip.lodging_region으로 해소 / 경기 "광주시" 오분류 방지
- [x] 테스트: 체크인·체크아웃으로 박수 계산(상한·박수 초과 검사)
- [x] 테스트: 조문 금액 사용(제18조·10분의 3·일비 1/2), 파싱 실패 시 확인필요
- [x] 테스트: 상한 초과 추가지급 시 제16조② 기한 안내
- [x] 구현 → 통과

### Task 4: 추출·입력 견고성
- [x] 테스트: 전송오류·503 재시도 후 성공, 400은 즉시 전달 / 영수증 1장 예외 → EXTRACT_FAILED, 나머지 정상
- [x] 테스트: EXIF 회전 반영(회전 이미지 캐시 키 분리), HEIC 수집, 이전 manifest 재사용(렌더 생략)
- [x] 테스트: 숫자 없는 승인번호·부분 일치 금액 경고
- [x] 구현 → 통과

### Task 5: 파이프라인 연결
- [x] 테스트: 법령 조회를 추출보다 먼저 / 한 출장 ingest·추출 실패가 일괄을 멈추지 않음 / 단일 출장 실행에서도 다른 출장과의 중복 표시 / 동시 실행 BusyError / 오프라인 폴백 안내가 결과·보고서에 들어감 / REPORT_VERSION이 fingerprint에 반영
- [x] 구현 → 통과

### Task 6: 웹
- [x] 테스트: 추출 작업은 법령 실패와 무관하게 성공 / 판정 화면이 오프라인 폴백 규정으로 열리고 안내 표시 / 파일 바뀜 상태면 서류 만들기 버튼 없음·POST 거부 / 잘못된 입력은 HTML 오류 화면(400) / 코드 KeyError는 404로 가리지 않음 / 개정 배너 / CDN 링크 없음·로컬 글꼴 제공
- [x] 구현(서비스·라우트·템플릿·글꼴 서브셋) → 통과

### Task 7: 운영·문서·검증
- [x] run-app.sh `uv sync` 오프라인 우선, README·SKILL 오프라인·환경변수 설명
- [x] 전체 단위 테스트, 통합 테스트(`-m integration`)와 실제 웹 E2E, npm 저장소 차단 상태에서 서버 기동 재확인
- [x] 켠 서비스 정리
