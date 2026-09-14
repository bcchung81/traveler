# 출장 삭제(휴지통)·홈 카드 축소 Implementation Plan

> **For agentic workers:** 서브에이전트 없이 메인 세션에서 인라인 실행. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 출장(정산 건)을 삭제할 수 있게 하되 휴지통으로 옮겨 복구·영구 삭제를 고를 수 있게 한다. 홈 카드는 내용을 정리해 세로 크기를 2/3(230px → 약 153px)로 줄인다.

**결정(Q&A):** 휴지통으로 이동 · 출장 단위(마지막 출장을 지워도 출장자 설정은 남김)

## Global Constraints
- 휴지통: `data/.trash/<id>/`(영수증·yaml + trash.json), `out/.trash/<id>/`(서류·작업 파일). 숨김 폴더라 CLI·홈 목록에서 제외
- id = `YYYYMMDD-HHMMSS_출장자_출장`(중복 시 `-2`)
- 복구 시 같은 이름의 출장이 있으면 `_2`로 되돌리고 out/work 경로 문자열을 고친다
- 읽기·서류 생성 작업이 도는 출장은 삭제하지 않는다. CLI와 동시 실행이면 안내
- 삭제·영구 삭제는 같은 화면 팝업으로 확인(스크립트가 없으면 확인 화면)
- 카드: 전체 링크 + 삭제 아이콘, 제목·기간/영수증·칩/인정액 3줄, 확인필요는 칩, 중복 문구 제거, `min-height: 154px`
- 버그 수정: 읽은 값 화면 입력 칸 배치 클래스 `.trip-grid` → `.trip-form-grid`(홈 3열 그리드와 충돌)

## Tasks
- [x] Task 1 코어: trash_trip·list_trash·restore_trip(충돌 시 _2+경로 치환)·purge_trash 테스트 → 구현
- [x] Task 2 웹: 카드 마크업·삭제 팝업·확인 화면·/trash 목록·복구·영구 삭제·되돌리기 배너·작업 중 거부 테스트 → 구현
- [x] Task 3 검증: 전체 테스트, Playwright로 카드 높이 측정(≈2/3)·삭제/복구 팝업 확인, 문서, 서비스 정리
