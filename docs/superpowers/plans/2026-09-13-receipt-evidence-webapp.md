# 여비정산 증빙 웹앱 (C안) Implementation Plan

> **For agentic workers:** 사용자는 서브에이전트 없이 메인 세션에서 직접 실행하기를 원한다 → superpowers:executing-plans로 인라인 실행. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 코어 파이프라인 위에 C안(코믹 패널) 디자인의 로컬 웹앱을 만든다. 구성은 출장 목록 홈과 4화면(올리기·읽은 값 확인·판정 검토·서류 완성)이며, 출장 생성·업로드·설정 입력·추출 확인·확인필요 해소·HWPX 생성·미리보기·다운로드를 브라우저에서 한다.

**Architecture:** `receipt_evidence.web` 패키지로 구성한다. FastAPI 앱 팩토리(`create_app`), 폴더 계약을 다루는 `TripService`, 단일 작업 스레드 `JobManager`, llama-server 프로세스 `VlmManager`, 작업 본문 `actions`, Jinja2 템플릿과 HTMX(저장소 포함)로 이루어진다. 코어에는 `receipts.extracted.json`, `extract_trip`, `review_receipts`, `run_batch(summary=)`만 보강한다.

**Tech Stack:** FastAPI ≥0.115, Jinja2 ≥3.1, python-multipart ≥0.0.9, uvicorn(mcp 의존성으로 설치됨), htmx 2.0.4(`static/htmx.min.js`), pytest + `fastapi.testclient`.

**Spec:** docs/superpowers/specs/2026-09-13-receipt-evidence-webapp-design.md (코어: 2026-09-13-receipt-evidence-design.md)

## Global Constraints
- 코어 계획의 Global Constraints 전부 유지(경로·개인정보·커밋 트레일러·NFC 정규화·mcp 2.x)
- 웹 바인드는 루프백만(`127.0.0.1` 기본, 포트 `8765`). 허용 Host: `127.0.0.1`, `localhost`(테스트는 `testserver`)
- 상태 변경 POST는 `Origin`이 있으면 같은 출처만 허용(403)
- 업로드: 확장자 `.jpg .jpeg .png .pdf`, 파일당 30MB(`MAX_UPLOAD_BYTES = 30 * 1024 * 1024`)
- 이름: 출장자·출장지는 NFC, 1~60자, `/ \ NUL` 불가, `.`으로 시작 불가
- 작업 스레드 1개(VLM·MCP 직렬화). 테스트는 `JobManager(inline=True)`
- 템플릿 문구(테스트 계약): 홈 "새 정산"·"아직 출장이 없어요", VLM "준비됨"·"켜는 중"·"꺼짐", 1화면 "영수증 읽기 시작"·"영수증을 먼저 올려 주세요", 2화면 "AI가 읽은 값"·"다시 읽기", 3화면 "잠깐, 확인!"·"HWPX 증빙서류 만들기", 4화면 "서류가 완성됐어요"·"합계를 다시 읽어 맞춰 봤어요", 단계 패널 클래스 `step--current|done|future`
- 파일 업로드 스크립트는 `static/htmx.min.js` 외 CDN 스크립트 금지(폰트만 Google Fonts, 폴백 지정)
---

## File Structure
```
src/receipt_evidence/pipeline.py            (수정) receipts.extracted.json, find_job, extract_trip, TripReview, review_receipts, run_batch(summary)
src/receipt_evidence/cli.py                 (수정) web 서브커맨드
src/receipt_evidence/web/__init__.py
src/receipt_evidence/web/service.py         TripService, TripSummary, FileInfo, VersionInfo, InvalidName, MAX_UPLOAD_BYTES
src/receipt_evidence/web/jobs.py            Job, JobManager
src/receipt_evidence/web/vlm_process.py     VlmManager
src/receipt_evidence/web/actions.py         do_extract, do_warm_law, do_finalize
src/receipt_evidence/web/app.py             WebSettings, WebDeps, create_app, default_deps
src/receipt_evidence/web/templates/{base,home,upload,extract,review,result,_steps,_vlm,_job}.html
src/receipt_evidence/web/static/{app.css,htmx.min.js}
tests/helpers.py                            (수정) TRAVELER, law_from, doc_fake, rail, png_bytes
tests/test_pipeline.py                      (수정) helpers 사용
tests/test_pipeline_hooks.py
tests/web/__init__.py, tests/web/conftest.py
tests/web/test_service.py, test_jobs.py, test_app.py, test_upload.py, test_extract_screen.py, test_review_screen.py, test_result_screen.py
tests/test_cli.py                           (수정) web 커맨드
tests/integration/test_web_e2e.py
```

---

### Task 17: 코어 보강 (웹 연동 훅)
**Files:** Modify `src/receipt_evidence/pipeline.py`, `tests/helpers.py`, `tests/test_pipeline.py`; Create `tests/test_pipeline_hooks.py`
**Interfaces:** Produces `find_job(data_dir, traveler, trip_id) -> TripJob`(없으면 `LookupError`), `extract_trip(data_dir, out_dir, clients, traveler, trip_id, opts: RunOptions | None = None, on_vlm_needed: Callable[[], None] | None = None) -> TripWork`, `TripReview(trip, receipts, decisions, totals, review_items)`, `review_receipts(job, receipts, law) -> TripReview`, `run_batch(..., summary: bool = True, on_vlm_needed: Callable[[], None] | None = None)`(캐시 미스 + VLM 무응답일 때만 훅 호출 후 재확인); `prepare_trip`가 `work/receipts.extracted.json` 저장. helpers: `TRAVELER: str`, `law_from(t: dict) -> FakeToolCaller`, `doc_fake(fail_on: str | None = None, render: bool = False) -> FakeToolCaller`, `rail(n, day, o, d) -> dict`, `png_bytes(rgb) -> bytes`

- [ ] **Step 1: helpers 이동 + 실패하는 테스트**
`tests/test_pipeline.py`의 `TRAVELER/_law/_doc/_rail`을 `tests/helpers.py`의 `TRAVELER/law_from/doc_fake/rail`로 옮기고 test_pipeline은 그것을 import(동작 동일). `doc_fake(render=True)`는 `render_document` 호출 시 `output_path`에 `<html><body>preview</body></html>`를 쓴다.
```python
# tests/test_pipeline_hooks.py
import json
from datetime import date
import pytest
from helpers import TRAVELER, ColorVlm, color_png, doc_fake, law_from, rail
from receipt_evidence.law import get_law_snapshot
from receipt_evidence.pipeline import Clients, extract_trip, find_job, review_receipts, run_batch

def _setup(tmp_path, t):
    data, out = tmp_path / "data", tmp_path / "out"
    trav = data / "정백철"; trip_dir = trav / "2026-07-09_서울"; trip_dir.mkdir(parents=True)
    (trav / "traveler.yaml").write_text(TRAVELER, encoding="utf-8")
    (trip_dir / "trip.yaml").write_text("start_date: 2026-07-09\nend_date: 2026-07-10\ndestination_region: 서울\n", encoding="utf-8")
    color_png(trip_dir / "k1.png", (10, 20, 30))
    clients = Clients(vlm=ColorVlm({(10, 20, 30): rail(1, date(2026, 7, 9), "나주", "용산")}), law=law_from(t), doc=doc_fake())
    return data, out, trip_dir, clients

def test_extract_trip_writes_extracted_and_applies_overrides(tmp_path, law_fixture_text):
    data, out, trip_dir, clients = _setup(tmp_path, law_fixture_text)
    work = extract_trip(data, out, clients, "정백철", "2026-07-09_서울")
    rid = work.receipts[0].receipt_id
    extracted = out / "정백철" / "2026-07-09_서울" / "work" / "receipts.extracted.json"
    assert [r["receipt_id"] for r in json.loads(extracted.read_text(encoding="utf-8"))] == [rid]
    (trip_dir / "overrides.yaml").write_text(f"{rid}:\n  amount: 50000\n", encoding="utf-8")
    work2 = extract_trip(data, out, clients, "정백철", "2026-07-09_서울")
    assert work2.receipts[0].amount == 50000 and work2.cache_hits == 1
    assert json.loads(extracted.read_text(encoding="utf-8"))[0]["amount"] == 48200

def test_extract_trip_unknown_trip_raises(tmp_path, law_fixture_text):
    data, out, _, clients = _setup(tmp_path, law_fixture_text)
    with pytest.raises(LookupError):
        extract_trip(data, out, clients, "정백철", "2026-01-01_없음")

def test_review_receipts_matches_finalize_without_summary(tmp_path, law_fixture_text):
    data, out, _, clients = _setup(tmp_path, law_fixture_text)
    work = extract_trip(data, out, clients, "정백철", "2026-07-09_서울")
    law = get_law_snapshot(clients.law, out / ".cache", date.today())
    review = review_receipts(find_job(data, "정백철", "2026-07-09_서울"), work.receipts, law)
    assert review.totals == {"claimed": 48200, "approved": 148200, "review": 0} and not review.trip.proposed
    res = run_batch(data, out, clients, run_id="w1", summary=False).results[0]
    assert res.totals == review.totals and not list(out.glob("summary-*"))

def test_on_vlm_needed_called_only_on_cache_miss_when_down(tmp_path, law_fixture_text):
    data, out, trip_dir, clients = _setup(tmp_path, law_fixture_text)
    clients.vlm._healthy = False
    calls = []
    def bring_up():
        calls.append(1); clients.vlm._healthy = True
    extract_trip(data, out, clients, "정백철", "2026-07-09_서울", on_vlm_needed=bring_up)
    assert calls == [1]
    clients.vlm._healthy = False
    extract_trip(data, out, clients, "정백철", "2026-07-09_서울", on_vlm_needed=bring_up)  # 전부 캐시 → 훅 호출 안 함
    run_batch(data, out, clients, run_id="w2", summary=False, on_vlm_needed=bring_up)
    assert calls == [1]
    color_png(trip_dir / "k2.png", (40, 50, 60))
    clients.vlm.specs[(40, 50, 60)] = rail(2, date(2026, 7, 10), "용산", "나주")
    clients.vlm._healthy = False
    with pytest.raises(RuntimeError, match="llama-server"):
        extract_trip(data, out, clients, "정백철", "2026-07-09_서울", on_vlm_needed=lambda: None)  # 훅 뒤에도 무응답
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_pipeline_hooks.py` → `ImportError: cannot import name 'extract_trip'`
- [ ] **Step 3: 구현** — `prepare_trip`에서 `validated = validate_all(...)` 직후 `_dump(work / "receipts.extracted.json", validated)`. `find_job`은 `discover(data_dir, [traveler], [trip_id])` 첫 항목. `extract_trip`은 run_batch의 ingest→캐시 미스 시 health 확인→`prepare_trip`을 출장 1건에 적용. `review_receipts`는 `resolve_trip`→`apply_cross_checks`→`decide_all`→`totals`/`review_items`를 계산해 `TripReview`로 반환하고, `finalize_trip`은 이것을 호출하도록 바꾼다. `run_batch(summary=False)`면 `write_summary`를 건너뛰고 경로는 `""`.
- [ ] **Step 4: 통과 확인** — `uv run pytest -q` → 전체 passed
- [ ] **Step 5: 커밋** — `feat: 웹 연동용 코어 훅(extract_trip·review_receipts·추출 원본 보존)`

---

### Task 18: TripService (폴더 계약 읽기·쓰기)
**Files:** Modify `pyproject.toml`(fastapi, jinja2, python-multipart); Create `src/receipt_evidence/web/__init__.py`, `src/receipt_evidence/web/service.py`, `tests/web/__init__.py`, `tests/web/test_service.py`
**Interfaces:**
- `MAX_UPLOAD_BYTES = 30 * 1024 * 1024`, `class InvalidName(ValueError)`
- `FileInfo(name: str, size: int, kind: str)`(kind: "사진"|"PDF"), `VersionInfo(version: int, hwpx: Path, report_md: Path, preview_html: Path | None, changes_md: Path | None, approved: int, review_count: int, verify_ok: bool | None)`
- `TripSummary(traveler, trip_id, start_date: date | None, end_date: date | None, destination: str, files: int, extracted: bool, stale: bool, version: int | None, claimed: int | None, approved: int | None, review_count: int | None, verify_ok: bool | None, proposed: bool, stage: str)` — stage ∈ `empty|uploaded|extracted|documented`
- `TripService(data_dir: Path, out_dir: Path)`: `trip_dir(traveler, trip_id) -> Path`, `trip_out(traveler, trip_id) -> Path`, `create_trip(traveler, start_date: date, destination: str) -> tuple[str, str]`, `save_files(traveler, trip_id, files: list[tuple[str, bytes]]) -> list[str]`, `list_files(traveler, trip_id) -> list[FileInfo]`, `delete_file(traveler, trip_id, name)`, `load_profile(traveler) -> TravelerProfile`, `save_profile(traveler, fields: dict[str, str])`, `load_trip_yaml(traveler, trip_id) -> dict`, `save_trip_yaml(traveler, trip_id, fields: dict[str, str])`, `receipts(traveler, trip_id) -> list[Receipt]`(extracted + overrides, 추출 전이면 `[]`), `save_override(traveler, trip_id, receipt_id, fields: dict[str, str], clear_warnings: list[str])`(없는 id면 `KeyError`), `images_for(traveler, trip_id) -> dict[str, list[str]]`, `image_path(traveler, trip_id, image_id) -> Path`, `cached_law(today: date) -> LawSnapshot | None`, `trip_status(traveler, trip_id) -> TripSummary`, `list_trips() -> list[TripSummary]`, `versions(traveler, trip_id) -> list[VersionInfo]`, `latest_result(traveler, trip_id) -> PipelineResult | None`, `version_file(traveler, trip_id, version: int, name: str) -> Path`(name ∈ evidence.hwpx|preview.html|changes.md|report.md, 없으면 `FileNotFoundError`)

- [ ] **Step 1: 실패하는 테스트**
```python
# tests/web/test_service.py
import unicodedata
from datetime import date
import pytest, yaml
from helpers import TRAVELER, ColorVlm, color_png, doc_fake, law_from, rail
from receipt_evidence.law import get_law_snapshot
from receipt_evidence.pipeline import Clients, extract_trip, run_batch
from receipt_evidence.web.service import MAX_UPLOAD_BYTES, InvalidName, TripService

def _svc(tmp_path):
    return TripService(tmp_path / "data", tmp_path / "out")

def _extracted(tmp_path, t):
    s = _svc(tmp_path)
    traveler, trip_id = s.create_trip("정백철", date(2026, 7, 9), "서울")
    (s.data_dir / traveler / "traveler.yaml").write_text(TRAVELER, encoding="utf-8")
    color_png(s.trip_dir(traveler, trip_id) / "k1.png", (10, 20, 30))
    clients = Clients(vlm=ColorVlm({(10, 20, 30): rail(1, date(2026, 7, 9), "나주", "용산")}), law=law_from(t), doc=doc_fake())
    extract_trip(s.data_dir, s.out_dir, clients, traveler, trip_id)
    return s, traveler, trip_id, clients

def test_create_trip_and_save_files(tmp_path):
    s = _svc(tmp_path)
    traveler, trip_id = s.create_trip(unicodedata.normalize("NFD", "정백철"), date(2026, 7, 9), "서울")
    assert (traveler, trip_id) == ("정백철", "2026-07-09_서울") and s.trip_dir(traveler, trip_id).is_dir()
    saved = s.save_files(traveler, trip_id, [("../../k1.png", b"a"), ("k1.png", b"b"), ("k1.png", b"a"), ("영수증.PDF", b"c")])
    assert saved == ["k1.png", "k1-2.png", "k1.png", "영수증.PDF"]
    assert [f.name for f in s.list_files(traveler, trip_id)] == ["k1-2.png", "k1.png", "영수증.PDF"]
    assert [f.kind for f in s.list_files(traveler, trip_id)] == ["사진", "사진", "PDF"]
    s.delete_file(traveler, trip_id, "k1-2.png")
    assert [f.name for f in s.list_files(traveler, trip_id)] == ["k1.png", "영수증.PDF"]

def test_rejects_bad_names_types_and_sizes(tmp_path):
    s = _svc(tmp_path); s.create_trip("정백철", date(2026, 7, 9), "서울")
    for bad in ("..", ".hidden", "a/b", "a\\b", "", "x" * 61):
        with pytest.raises(InvalidName):
            s.trip_dir(bad, "2026-07-09_서울")
    with pytest.raises(ValueError, match="확장자"):
        s.save_files("정백철", "2026-07-09_서울", [("memo.txt", b"x")])
    with pytest.raises(ValueError, match="30MB"):
        s.save_files("정백철", "2026-07-09_서울", [("big.jpg", b"x" * (MAX_UPLOAD_BYTES + 1))])
    with pytest.raises(InvalidName):
        s.delete_file("정백철", "2026-07-09_서울", "../traveler.yaml")

def test_profile_and_trip_yaml_roundtrip(tmp_path):
    s = _svc(tmp_path); traveler, trip_id = s.create_trip("정백철", date(2026, 7, 9), "서울")
    s.save_profile(traveler, {"grade": "제2호", "workplace_region": "나주", "approval": "담당, 팀장 ,부장", "org": ""})
    assert yaml.safe_load((s.data_dir / traveler / "traveler.yaml").read_text(encoding="utf-8")) == {"grade": "제2호", "workplace_region": "나주", "approval": ["담당", "팀장", "부장"]}
    assert s.load_profile(traveler).approval == ["담당", "팀장", "부장"]
    s.save_trip_yaml(traveler, trip_id, {"start_date": "2026-07-09", "end_date": "2026-07-10", "destination_region": "서울", "purpose": "", "unknown": "x", "route_stations": "나주, 용산"})
    s.save_trip_yaml(traveler, trip_id, {"lodging_region": "서울", "official_vehicle": "on"})
    y = s.load_trip_yaml(traveler, trip_id)
    assert y["start_date"] == date(2026, 7, 9) and y["lodging_region"] == "서울" and y["purpose"] is None and "unknown" not in y
    assert y["route_stations"] == ["나주", "용산"] and y["official_vehicle"] is True

def test_status_stages_and_staleness(tmp_path, law_fixture_text):
    s = _svc(tmp_path); traveler, trip_id = s.create_trip("정백철", date(2026, 7, 9), "서울")
    assert s.trip_status(traveler, trip_id).stage == "empty"
    s2, traveler, trip_id, clients = _extracted(tmp_path / "b", law_fixture_text)
    st = s2.trip_status(traveler, trip_id)
    assert (st.stage, st.files, st.extracted, st.stale, st.proposed) == ("extracted", 1, True, False, True)
    color_png(s2.trip_dir(traveler, trip_id) / "k2.png", (40, 50, 60))
    st = s2.trip_status(traveler, trip_id)
    assert st.stale and st.stage == "uploaded" and [t.trip_id for t in s2.list_trips()] == [trip_id]

def test_receipts_with_overrides_and_diff_only_save(tmp_path, law_fixture_text):
    s, traveler, trip_id, _ = _extracted(tmp_path, law_fixture_text)
    r = s.receipts(traveler, trip_id)[0]
    s.save_override(traveler, trip_id, r.receipt_id, {"amount": "48,200", "merchant": "코레일", "service_date": "2026-07-09", "nights": ""}, clear_warnings=["MISSING_BIZNO"])
    path = s.trip_dir(traveler, trip_id) / "overrides.yaml"
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {r.receipt_id: {"merchant": "코레일", "clear_warnings": ["MISSING_BIZNO"]}}
    assert s.receipts(traveler, trip_id)[0].merchant == "코레일"
    s.save_override(traveler, trip_id, r.receipt_id, {"merchant": "한국철도공사"}, clear_warnings=[])
    assert not path.exists() or yaml.safe_load(path.read_text(encoding="utf-8")) in (None, {})
    with pytest.raises(KeyError):
        s.save_override(traveler, trip_id, "nope-p1", {"amount": "1"}, clear_warnings=[])

def test_images_versions_and_law_cache(tmp_path, law_fixture_text):
    s, traveler, trip_id, clients = _extracted(tmp_path, law_fixture_text)
    rid = s.receipts(traveler, trip_id)[0].receipt_id
    assert s.images_for(traveler, trip_id)[rid] == [rid] and s.image_path(traveler, trip_id, rid).exists()
    with pytest.raises(InvalidName):
        s.image_path(traveler, trip_id, "../../x")
    assert s.cached_law(date(2000, 1, 1)) is None
    run_batch(s.data_dir, s.out_dir, clients, summary=False)
    assert s.cached_law(date.today()).mst == "287535"
    v = s.versions(traveler, trip_id)
    assert [x.version for x in v] == [1] and v[0].hwpx.exists() and v[0].verify_ok is True
    assert s.trip_status(traveler, trip_id).stage == "documented" and s.latest_result(traveler, trip_id).version == 1
    assert s.version_file(traveler, trip_id, 1, "evidence.hwpx").exists()
    with pytest.raises(InvalidName):
        s.version_file(traveler, trip_id, 1, "../latest.json")
    with pytest.raises(FileNotFoundError):
        s.version_file(traveler, trip_id, 7, "evidence.hwpx")
```
- [ ] **Step 2: 실패 확인** — `ModuleNotFoundError: No module named 'receipt_evidence.web'`
- [ ] **Step 3: 구현 요점**
  - 이름 검증 `_name(s)`: `nfc(s).strip()`, 빈 값·60자 초과·`/ \ \x00` 포함·`.` 시작이면 `InvalidName`. `trip_dir`는 `(data_dir / t / trip).resolve()`가 `data_dir.resolve()` 하위인지 재확인.
  - 업로드 이름: `Path(nfc(name).replace("\\", "/")).name` → 검증 → 같은 이름이 있고 내용 해시가 다르면 `stem-2`, `stem-3`… (같은 내용이면 같은 이름 재사용).
  - `save_profile`: 키 `position, grade, org, dept, workplace_region, approval`만. 빈 문자열은 제거, approval은 쉼표 분리·공백 제거 목록. 기존 파일과 병합.
  - `save_trip_yaml`: `TRIP_YAML_FIELDS`만, 폼 문자열 변환(날짜 `date.fromisoformat`, `route_stations` 쉼표 목록, `official_vehicle/within_workplace` "on"/"true" → bool, `duration_hours` float, 빈 값 None). 기존과 병합.
  - `receipts`: `receipts.extracted.json` → `apply_overrides(…, load_overrides(trip_dir))`.
  - `save_override`: 추출값(`extracted`)과 폼 값을 정규화(amount/nights `int`, 날짜 `date`, 빈 값 None, paid_at은 `parse_datetime`)해서 비교해, 다른 필드만 `overrides[rid]`에 넣고 같은 필드는 제거. `clear_warnings`는 목록이 비면 키 제거. 빈 항목은 삭제하고, 전체가 비면 파일 삭제.
  - `stage`: 파일 0 → empty. 추출 안 됨·stale → uploaded. 버전이 있고 `v<N>/result.json` mtime ≥ max(traveler.yaml, trip.yaml, overrides.yaml, receipts.extracted.json mtime) → documented. 그 외 → extracted. `stale`은 현재 파일들의 `sha256_file` 집합 ≠ `work/manifest.json`의 sha256 집합.
  - `image_path`: `^[0-9a-f]{12}-p\d+$`만 허용 → `trip_out/work/images/<id>.png`.
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/web/test_service.py` → `6 passed`
- [ ] **Step 5: 커밋** — `feat: 웹앱 TripService(폴더 계약·업로드·설정·사용자 확인값·상태)`

---

### Task 19: JobManager · VlmManager
**Files:** Create `src/receipt_evidence/web/jobs.py`, `src/receipt_evidence/web/vlm_process.py`, `tests/web/test_jobs.py`
**Interfaces:** `Job(key, kind, state: str, message: str, result: object, started_at: datetime | None, finished_at: datetime | None)`, `JobManager(inline: bool = False)`: `submit(key, kind, fn) -> Job`(같은 key가 queued/running이면 기존 Job 반환), `get(key) -> Job | None`, `shutdown()`. `VlmManager(health: Callable[[], bool], start_cmd: list[str], cwd: Path, popen=subprocess.Popen, sleep=time.sleep, clock=time.monotonic)`: 공개 속성 `health`, `popen`; `status() -> "ready"|"starting"|"stopped"`, `ensure_ready(timeout: float = 180.0, poll: float = 1.0) -> None`(필요할 때만 시작 후 준비될 때까지 대기, 시간 초과·프로세스 종료 시 RuntimeError), `stop()`(자신이 띄운 프로세스만 종료)

- [ ] **Step 1: 실패하는 테스트**
```python
# tests/web/test_jobs.py
import threading, time
import pytest
from receipt_evidence.web.jobs import JobManager
from receipt_evidence.web.vlm_process import VlmManager

def _boom():
    raise RuntimeError("llama-server가 응답하지 않음")

def test_inline_jobs_record_result_and_error():
    jm = JobManager(inline=True)
    j = jm.submit("a", "extract", lambda: 3)
    assert (j.state, j.result) == ("done", 3) and j.finished_at is not None
    e = jm.submit("b", "extract", _boom)
    assert e.state == "error" and "llama-server" in e.message and jm.get("b") is e

def test_threaded_jobs_dedupe_running_key():
    gate = threading.Event()
    jm = JobManager()
    try:
        j1 = jm.submit("t", "extract", lambda: gate.wait(5) and "ok")
        assert jm.submit("t", "extract", lambda: "second") is j1 and j1.state in ("queued", "running")
        gate.set()
        for _ in range(200):
            if j1.state == "done":
                break
            time.sleep(0.01)
        assert j1.state == "done" and j1.result == "ok"
        assert jm.submit("t", "finalize", lambda: "new") is not j1
    finally:
        jm.shutdown()

class FakeProc:
    def __init__(self):
        self.alive, self.terminated = True, False
    def poll(self):
        return None if self.alive else 0
    def terminate(self):
        self.alive, self.terminated = False, True
    def wait(self, timeout=None):
        return 0
    def kill(self):
        self.alive = False

def test_vlm_manager_ensure_ready_starts_waits_and_stops(tmp_path):
    polls, started = {"n": 0}, []
    def health():
        polls["n"] += 1; return polls["n"] > 3        # 세 번째 확인까지는 아직 로딩 중
    def popen(cmd, **kw):
        started.append((cmd, kw["cwd"])); return FakeProc()
    m = VlmManager(health=health, start_cmd=["bash", "scripts/start_vlm.sh"], cwd=tmp_path, popen=popen, sleep=lambda s: None)
    m.ensure_ready(timeout=10)
    assert len(started) == 1 and m.status() == "ready"
    proc = m._proc; m.stop()
    assert proc.terminated and m._proc is None

def test_vlm_manager_leaves_external_server_alone(tmp_path):
    started = []
    m = VlmManager(health=lambda: True, start_cmd=["x"], cwd=tmp_path, popen=lambda *a, **k: started.append(1))
    m.ensure_ready()
    assert started == []
    m.stop()  # 남이 켠 서버는 끄지 않는다

def test_vlm_manager_timeout_and_crash(tmp_path):
    t = {"now": 0.0}
    def sleep(s):
        t["now"] += s
    m = VlmManager(health=lambda: False, start_cmd=["x"], cwd=tmp_path, popen=lambda *a, **k: FakeProc(), sleep=sleep, clock=lambda: t["now"])
    with pytest.raises(RuntimeError, match="준비되지"):
        m.ensure_ready(timeout=5, poll=1)
    assert m._proc is None  # 시간 초과 시 띄운 프로세스를 정리
    dead = FakeProc(); dead.alive = False
    m2 = VlmManager(health=lambda: False, start_cmd=["x"], cwd=tmp_path, popen=lambda *a, **k: dead, sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="종료"):
        m2.ensure_ready(timeout=5)
```
- [ ] **Step 2: 실패 확인** — `ModuleNotFoundError: No module named 'receipt_evidence.web.jobs'`
- [ ] **Step 3: 구현 요점** — `ThreadPoolExecutor(max_workers=1)`, 작업 본문에서 state를 running으로 바꾸고 결과·예외(`f"{type(e).__name__}: {e}"`)·시각을 기록한다. 딕셔너리 접근은 Lock으로 보호. inline이면 submit 안에서 즉시 실행. VlmManager.ensure_ready: health가 True면 즉시 반환. 아니면 자신이 띄운 프로세스가 없거나 죽었을 때만 `popen(start_cmd, cwd=cwd, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)`. 이후 `clock()` 기준 timeout까지 `poll`초마다 health를 확인한다. 프로세스가 종료되면 RuntimeError("llama-server가 시작 중 종료됨"), 시간 초과면 stop() 후 RuntimeError("llama-server가 N초 안에 준비되지 않음"). stop은 `terminate()` → `wait(timeout=10)`, 실패하면 `kill()` 후 `_proc = None`.
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/web/test_jobs.py` → `5 passed`
- [ ] **Step 5: 커밋** — `feat: 웹앱 작업 관리자와 llama-server 프로세스 관리자`

---

### Task 20: 앱 뼈대 · 보안 · 홈 · VLM 조각
**Files:** Create `src/receipt_evidence/web/app.py`, `src/receipt_evidence/web/actions.py`, `templates/base.html`, `templates/_steps.html`, `templates/_vlm.html`, `templates/_job.html`, `templates/home.html`, `static/app.css`, `static/htmx.min.js`(htmx 2.0.4), `tests/web/conftest.py`, `tests/web/test_app.py`
**Interfaces:**
- `WebSettings(data_dir: Path, out_dir: Path, host: str = "127.0.0.1", port: int = 8765, vlm_url: str = "http://127.0.0.1:8088", allowed_hosts: list[str] = ["127.0.0.1", "localhost"])`
- `WebDeps(service: TripService, jobs: JobManager, vlm: VlmManager, clients: Callable[[], ContextManager[Clients]])`
- `do_finalize`도 `run_batch(..., on_vlm_needed=vlm.ensure_ready)` 후 `finally: vlm.stop()`(평소엔 캐시 적중이라 켜지 않음)
- `create_app(settings: WebSettings, deps: WebDeps | None = None) -> FastAPI` (deps가 없으면 `default_deps(settings)`; 종료 시 `jobs.shutdown()`·`vlm.stop()`)
- `actions.do_extract(settings, clients, vlm: VlmManager, traveler, trip_id) -> int`(`extract_trip(..., on_vlm_needed=vlm.ensure_ready)` → 법령 일자 캐시 예열 → `finally: vlm.stop()`), `do_warm_law(settings, clients) -> str`, `do_finalize(settings, clients, traveler, trip_id) -> PipelineResult`(`run_batch(summary=False)` 1건, 오류면 RuntimeError, `render_document` html → `v<N>/preview.html`, 렌더 실패는 무시)

- [ ] **Step 1: 실패하는 테스트**
```python
# tests/web/conftest.py
from contextlib import contextmanager
from datetime import date
import pytest
from fastapi.testclient import TestClient
from helpers import ColorVlm, doc_fake, law_from, rail, spec
from receipt_evidence.pipeline import Clients
from receipt_evidence.web.app import WebDeps, WebSettings, create_app
from receipt_evidence.web.jobs import JobManager
from receipt_evidence.web.service import TripService
from receipt_evidence.web.vlm_process import VlmManager

SPECS = {(10, 20, 30): rail(1, date(2026, 7, 9), "나주", "용산"), (40, 50, 60): rail(2, date(2026, 7, 10), "용산", "나주"),
         (70, 80, 90): spec("숙박", 100000, "68325420", merchant="(주)예시숙박", service_date="2026-08-21")}

@pytest.fixture
def web(tmp_path, law_fixture_text):
    settings = WebSettings(data_dir=tmp_path / "data", out_dir=tmp_path / "out", allowed_hosts=["testserver"])
    vlm = ColorVlm(SPECS)
    @contextmanager
    def clients():
        yield Clients(vlm=vlm, law=law_from(law_fixture_text), doc=doc_fake(render=True))
    deps = WebDeps(service=TripService(settings.data_dir, settings.out_dir), jobs=JobManager(inline=True),
                   vlm=VlmManager(health=lambda: True, start_cmd=["true"], cwd=tmp_path, popen=lambda *a, **k: None), clients=clients)
    with TestClient(create_app(settings, deps), follow_redirects=False) as client:
        client.deps, client.vlm_fake = deps, vlm
        yield client
```
```python
# tests/web/test_app.py
from test_jobs import FakeProc

def test_home_empty_and_vlm_badge(web):
    r = web.get("/")
    assert r.status_code == 200 and "새 정산" in r.text and "아직 출장이 없어요" in r.text and 'id="vlm-status"' in r.text
    assert '/static/htmx.min.js' in r.text and "준비됨" in web.get("/vlm").text
    assert web.get("/static/app.css").status_code == 200

def test_vlm_badge_states_and_no_start_button(web):
    web.deps.vlm.health = lambda: False
    r = web.get("/vlm")
    assert "꺼짐" in r.text and "필요할 때 자동으로 켜요" in r.text and "/vlm/start" not in r.text
    web.deps.vlm._proc = FakeProc()
    r = web.get("/vlm")
    assert "켜는 중" in r.text and 'hx-trigger="every 3s"' in r.text

def test_security_host_origin_and_paths(web):
    assert web.get("/", headers={"host": "evil.example"}).status_code == 400
    assert web.post("/new", data={}, headers={"origin": "http://evil.example"}).status_code == 403
    assert web.post("/new", data={}, headers={"origin": "http://testserver"}).status_code == 400  # 출처는 통과, 필수값 없음
    assert web.get("/t/.hidden/2026-07-09_서울/upload").status_code == 400
```
- [ ] **Step 2: 실패 확인** — `ModuleNotFoundError: No module named 'receipt_evidence.web.app'`
- [ ] **Step 3: 구현 요점** — `Jinja2Templates(directory=Path(__file__).parent / "templates")`, 필터 `won`(천 단위 콤마)·`kdate`. `StaticFiles` 마운트. `TrustedHostMiddleware(allowed_hosts=settings.allowed_hosts)`. POST·DELETE 요청에서 `Origin`이 있고 `f"{request.url.scheme}://{request.headers['host']}"`와 다르면 403인 미들웨어. `InvalidName` → 400, `FileNotFoundError`·`KeyError`·`LookupError` → 404 예외 핸들러. 홈은 `service.list_trips()`를 출장자별로 묶어 카드로 렌더링하고, 비었으면 "아직 출장이 없어요". `_vlm.html`(버튼 없음): ready "준비됨"(시안), starting "켜는 중" + `hx-get="/vlm" hx-trigger="every 3s" hx-swap="outerHTML"`, stopped "꺼짐 · 필요할 때 자동으로 켜요". `_steps.html` 매크로 `steps(active: int, done: set[int])`로 4패널을 그린다. htmx는 `curl -sfL https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js`로 받아 커밋한다.
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/web/test_app.py` → `3 passed`
- [ ] **Step 5: 커밋** — `feat: 웹앱 뼈대(보안 미들웨어·홈·VLM 상태)와 C안 스타일`

---

### Task 21: 1화면 올리기 · 새 정산
**Files:** Create `templates/upload.html`, `tests/web/test_upload.py`; Modify `app.py`
**Interfaces:** routes `GET /new`, `POST /new`, `GET /t/{traveler}/{trip_id}/upload?error=`, `POST …/files`, `POST …/files/delete`, `POST …/info`, `POST …/extract`; helper `new_trip(client, files=(("k1.png", (10, 20, 30)),))`는 tests/helpers.py에 추가

- [ ] **Step 1: 실패하는 테스트**
```python
# tests/helpers.py 에 추가
def png_bytes(rgb, size=(240, 480)) -> bytes:
    buf = io.BytesIO(); Image.new("RGB", size, rgb).save(buf, "PNG"); return buf.getvalue()

NEW_TRIP_FORM = {"traveler": "정백철", "grade": "제2호", "workplace_region": "나주", "approval": "담당, 팀장",
                 "destination_region": "서울", "start_date": "2026-07-09", "end_date": "2026-07-10", "purpose": "회의", "route_stations": "나주, 용산"}

def new_trip(client, files=(("k1.png", (10, 20, 30)),)):
    return client.post("/new", data=NEW_TRIP_FORM, files=[("files", (n, png_bytes(rgb), "image/png")) for n, rgb in files])
```
```python
# tests/web/test_upload.py
from datetime import date
from urllib.parse import unquote
from helpers import new_trip, png_bytes

BASE = "/t/정백철/2026-07-09_서울"

def test_new_trip_creates_folder_yaml_and_files(web):
    assert web.get("/new").status_code == 200
    r = new_trip(web)
    assert r.status_code == 303 and unquote(r.headers["location"]) == f"{BASE}/upload"
    s = web.deps.service
    assert [f.name for f in s.list_files("정백철", "2026-07-09_서울")] == ["k1.png"]
    assert s.load_profile("정백철").grade == "제2호" and s.load_trip_yaml("정백철", "2026-07-09_서울")["route_stations"] == ["나주", "용산"]
    page = web.get(f"{BASE}/upload")
    assert page.status_code == 200 and "k1.png" in page.text and 'value="회의"' in page.text and "step--current" in page.text
    assert "영수증 읽기 시작" in page.text

def test_upload_add_delete_info_and_errors(web):
    new_trip(web)
    assert web.post(f"{BASE}/files", files=[("files", ("k2.png", png_bytes((40, 50, 60)), "image/png"))]).status_code == 303
    assert "k2.png" in web.get(f"{BASE}/upload").text
    assert web.post(f"{BASE}/files", files=[("files", ("memo.txt", b"x", "text/plain"))]).status_code == 400
    assert web.post(f"{BASE}/files/delete", data={"name": "k2.png"}).status_code == 303
    assert "k2.png" not in web.get(f"{BASE}/upload").text
    r = web.post(f"{BASE}/info", data={"grade": "제1호", "end_date": "2026-07-11"})
    assert r.status_code == 303
    assert web.deps.service.load_profile("정백철").grade == "제1호"
    assert web.deps.service.load_trip_yaml("정백철", "2026-07-09_서울")["end_date"] == date(2026, 7, 11)
    assert web.deps.service.load_trip_yaml("정백철", "2026-07-09_서울")["purpose"] == "회의"

def test_extract_without_files_shows_message(web):
    web.deps.service.create_trip("정백철", date(2026, 7, 9), "서울")
    r = web.post(f"{BASE}/extract")
    assert r.status_code == 303 and "영수증을 먼저 올려 주세요" in web.get(unquote(r.headers["location"])).text
```
- [ ] **Step 2: 실패 확인** — `404`/`AssertionError`
- [ ] **Step 3: 구현 요점** — `POST /new`: `traveler`·`start_date`·`destination_region` 필수(없으면 400). `create_trip` → 프로필 키는 `save_profile`, 출장 키는 `save_trip_yaml` → `save_files` → 303. `/info`는 폼에 **있는 키만** 저장(없는 키는 기존값 유지). `/extract`는 파일이 0개면 `upload?error=nofiles`로, 있으면 `jobs.submit(key, "extract", lambda: do_extract(...))` 후 `…/extract`로 303. 템플릿은 Upload.dc.html 구성(드롭존 `<input type=file multiple name=files>` + 올린 파일 목록과 삭제 버튼, 출장 정보 폼, 로컬 처리 안내)을 따른다.
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/web/test_upload.py` → `3 passed`
- [ ] **Step 5: 커밋** — `feat: 웹앱 1화면(새 정산·영수증 올리기·출장 정보)`

---

### Task 22: 2화면 읽은 값 확인
**Files:** Create `templates/extract.html`, `tests/web/test_extract_screen.py`; Modify `app.py`
**Interfaces:** `GET …/extract?rid=`, `GET …/job`, `POST …/receipts/{rid}`(form 필드들 + `clear_warnings` 다중값 + `next`), `GET …/image/{image_id}`

- [ ] **Step 1: 실패하는 테스트**
```python
# tests/web/test_extract_screen.py
from urllib.parse import unquote
from helpers import new_trip

BASE = "/t/정백철/2026-07-09_서울"

def test_extract_flow_edit_and_image(web):
    new_trip(web, files=(("k1.png", (10, 20, 30)), ("stay.png", (70, 80, 90))))
    r = web.post(f"{BASE}/extract")
    assert r.status_code == 303 and unquote(r.headers["location"]) == f"{BASE}/extract"
    page = web.get(f"{BASE}/extract")
    rs = web.deps.service.receipts("정백철", "2026-07-09_서울")
    assert page.status_code == 200 and len(rs) == 2 and "AI가 읽은 값" in page.text and "48,200" in page.text
    stay = next(x for x in rs if x.amount == 100000)
    page = web.get(f"{BASE}/extract", params={"rid": stay.receipt_id})
    assert f"/image/{stay.receipt_id}" in unquote(page.text) and "(주)예시숙박" in page.text and "MISSING_BIZNO" in page.text
    img = web.get(f"{BASE}/image/{stay.receipt_id}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert web.get(f"{BASE}/image/..%2Fx").status_code in (400, 404)
    r = web.post(f"{BASE}/receipts/{stay.receipt_id}", data={"service_date": "2026-07-09", "region": "서울", "clear_warnings": ["MISSING_BIZNO"]})
    assert r.status_code == 303 and "rid=" in r.headers["location"]
    fixed = next(x for x in web.deps.service.receipts("정백철", "2026-07-09_서울") if x.receipt_id == stay.receipt_id)
    assert fixed.region == "서울" and "MISSING_BIZNO" not in fixed.warnings
    assert web.get(f"{BASE}/job").headers.get("HX-Refresh") == "true"

def test_extract_error_panel_when_vlm_down(web):
    new_trip(web)
    web.vlm_fake._healthy = False
    web.deps.vlm.health = lambda: False
    web.deps.vlm.popen = lambda *a, **k: None   # 켜기 실패 재현(프로세스 없음)
    web.post(f"{BASE}/extract")
    page = web.get(f"{BASE}/extract")
    assert page.status_code == 200 and "llama-server" in page.text and "다시 읽기" in page.text
```
- [ ] **Step 2: 실패 확인** — 404
- [ ] **Step 3: 구현 요점** — 페이지 상태 순서: 작업이 queued/running이면 `_job.html`(`hx-get="…/job" hx-trigger="every 2s"`)을 보여 준다. 작업이 error면 오류 패널(메시지 + "다시 읽기" 버튼 `hx-post`/form `…/extract`)을 보여 준다. 추출본이 있으면 3단 구성이다: 목록(검증 칩 "검증 통과"/"빈칸 있음"), 원본 이미지(`<img src="…/image/{id}">` 쪽마다), 필드 폼(구분 select, 가맹점·사업자번호·금액·결제일시·운행/체크인일·체크아웃·출발·도착·좌석·열차·승인번호·지역·박수, 경고 체크박스 `clear_warnings`). stale이면 "파일이 바뀌었어요" 배너와 "다시 읽기"를 띄운다. 추출본이 없으면 1화면으로 303. `/job`: 작업이 없거나 done·error면 `HX-Refresh: true`. `POST receipts`는 `next=review`면 review로, 아니면 `extract?rid=`로 303.
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/web/test_extract_screen.py` → `2 passed`
- [ ] **Step 5: 커밋** — `feat: 웹앱 2화면(읽은 값 확인·수정·원본 이미지)`

---

### Task 23: 3화면 판정 검토
**Files:** Create `templates/review.html`, `tests/web/test_review_screen.py`; Modify `app.py`
**Interfaces:** `GET …/review`, `POST …/resolve`(trip·프로필 해소 필드), `POST …/finalize`

- [ ] **Step 1: 실패하는 테스트**
```python
# tests/web/test_review_screen.py
from urllib.parse import unquote
from helpers import new_trip

BASE = "/t/정백철/2026-07-09_서울"
FILES = (("k1.png", (10, 20, 30)), ("k2.png", (40, 50, 60)), ("stay.png", (70, 80, 90)))

def test_review_decisions_and_resolve(web):
    new_trip(web, files=FILES)
    web.post(f"{BASE}/extract")
    page = web.get(f"{BASE}/review")
    assert page.status_code == 200 and "196,400" in page.text and "잠깐, 확인!" in page.text and "출장기간" in page.text
    assert "HWPX 증빙서류 만들기" in page.text
    stay = next(x for x in web.deps.service.receipts("정백철", "2026-07-09_서울") if x.amount == 100000)
    r = web.post(f"{BASE}/receipts/{stay.receipt_id}", data={"service_date": "2026-07-09", "region": "서울", "next": "review"})
    assert unquote(r.headers["location"]) == f"{BASE}/review"
    page = web.get(f"{BASE}/review")
    assert "296,400" in page.text and "잠깐, 확인!" not in page.text
    r = web.post(f"{BASE}/resolve", data={"lodging_region": "서울", "taxi_reason": ""})
    assert r.status_code == 303 and web.deps.service.load_trip_yaml("정백철", "2026-07-09_서울")["lodging_region"] == "서울"

def test_review_warms_law_when_cache_missing(web):
    new_trip(web)
    web.post(f"{BASE}/extract")
    for f in (web.deps.service.out_dir / ".cache" / "law").glob("*.json"):
        f.unlink()
    page = web.get(f"{BASE}/review")
    assert page.status_code == 200 and "148,200" in page.text

def test_review_redirects_before_extraction(web):
    new_trip(web)
    r = web.get(f"{BASE}/review")
    assert r.status_code == 303 and unquote(r.headers["location"]) == f"{BASE}/upload"
```
- [ ] **Step 2: 실패 확인** — 404
- [ ] **Step 3: 구현 요점** — 추출본이 없으면 upload로 303. `service.cached_law(date.today())`이 None이면 `jobs.submit(key+":law", "law", do_warm_law)`를 제출한다. inline이면 즉시 끝나므로 다시 조회하고, 아직이면 진행 조각을 보여 준다. `review_receipts(find_job(...), service.receipts(...), law)`로 합계 카드(인정·청구·확인필요), 판정 표(구분·내역·일자·청구·인정·판정 칩·근거), 확인필요 목록을 그린다. 확인필요가 있으면 "잠깐, 확인!" 스티커 카드를 띄운다. 숙박 확인필요 행에는 해당 영수증의 `service_date`·`region` 인라인 폼(`POST receipts/{rid}` + `next=review`)을, 택시·초과 사유·구분 미확정에는 `POST resolve`(taxi_reason, over_cap_reason, lodging_region, grade, start_date, end_date) 폼을 둔다. `resolve`는 grade는 프로필에, 나머지는 trip.yaml에 **있는 키만** 저장한다. `finalize`는 `jobs.submit(key, "finalize", do_finalize)` 후 result로 303.
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/web/test_review_screen.py` → `3 passed`
- [ ] **Step 5: 커밋** — `feat: 웹앱 3화면(판정 검토·확인필요 해소)`

---

### Task 24: 4화면 서류 완성 · 미리보기 · 다운로드 · 버전
**Files:** Create `templates/result.html`, `tests/web/test_result_screen.py`; Modify `app.py`
**Interfaces:** `GET …/result`, `GET …/v/{n}/evidence.hwpx`, `GET …/v/{n}/preview`, `GET …/v/{n}/changes`

- [ ] **Step 1: 실패하는 테스트**
```python
# tests/web/test_result_screen.py
from urllib.parse import unquote
from helpers import new_trip

BASE = "/t/정백철/2026-07-09_서울"

def test_finalize_result_preview_download_versions_and_home(web):
    new_trip(web)
    web.post(f"{BASE}/extract")
    r = web.post(f"{BASE}/finalize")
    assert r.status_code == 303 and unquote(r.headers["location"]) == f"{BASE}/result"
    page = web.get(f"{BASE}/result")
    text = unquote(page.text)
    assert page.status_code == 200 and "서류가 완성됐어요" in text and "148,200" in text and "v1" in text
    assert f"{BASE}/v/1/preview" in text and "합계를 다시 읽어 맞춰 봤어요" in text and f"{BASE}/v/1/evidence.hwpx" in text
    d = web.get(f"{BASE}/v/1/evidence.hwpx")
    assert d.status_code == 200 and d.content == b"PK" and "attachment" in d.headers["content-disposition"]
    p = web.get(f"{BASE}/v/1/preview")
    assert p.status_code == 200 and "default-src 'none'" in p.headers["content-security-policy"]
    assert web.get(f"{BASE}/v/9/evidence.hwpx").status_code == 404
    web.post(f"{BASE}/info", data={"purpose": "변경"})
    web.post(f"{BASE}/finalize")
    text = unquote(web.get(f"{BASE}/result").text)
    assert "v2" in text and web.get(f"{BASE}/v/2/changes").status_code == 200
    home = unquote(web.get("/").text)
    assert "2026-07-09_서울" in home and "v2" in home and "148,200" in home

def test_result_before_any_version_redirects_to_review(web):
    new_trip(web)
    web.post(f"{BASE}/extract")
    r = web.get(f"{BASE}/result")
    assert r.status_code == 303 and unquote(r.headers["location"]) == f"{BASE}/review"
```
- [ ] **Step 2: 실패 확인** — 404
- [ ] **Step 3: 구현 요점** — 작업이 진행 중이면 진행 조각, 오류면 오류 패널(메시지 + "다시 만들기")을 보여 준다. 버전이 없으면 review로 303. 최신 버전은 미리보기 iframe(`sandbox`, preview가 없으면 "미리보기를 만들지 못했어요"), 파일 카드(다운로드), 인정액, 합계 검증 카드("합계를 다시 읽어 맞춰 봤어요", 불일치면 경고), 문서에 들어간 내용, 확인필요가 남았으면 "숙박 확인하고 다시 만들기" 링크(review), 버전 이력(v번호·인정액·검증·changes 링크)으로 구성한다. 다운로드는 `FileResponse(filename=f"출장여비_증빙내역서_{traveler}_{trip_id}_v{n}.hwpx")`. preview는 `HTMLResponse` + CSP. changes는 `PlainTextResponse`.
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/web/test_result_screen.py` → `2 passed`
- [ ] **Step 5: 커밋** — `feat: 웹앱 4화면(서류 완성·미리보기·다운로드·버전 이력)`

---

### Task 25: CLI `web` + 문서
**Files:** Modify `src/receipt_evidence/cli.py`, `tests/test_cli.py`, `README.md`, `.claude/skills/receipt-evidence/SKILL.md`, `tests/test_skill_doc.py`
**Interfaces:** `receipt-evidence web [--host 127.0.0.1] [--port 8765] [--data data] [--out out] [--vlm-url …]`, `cli._serve_web(settings: WebSettings) -> None`, `cli.vlm_manager(vlm_url: str) -> VlmManager`(run·web 공용). `run`은 `run_batch(..., on_vlm_needed=vlm.ensure_ready)`를 쓰고 `finally: vlm.stop()`

- [ ] **Step 1: 실패하는 테스트**
```python
# tests/test_cli.py 에 추가
def test_web_command_refuses_non_loopback(monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "_serve_web", lambda settings: called.setdefault("s", settings))
    assert cli.main(["web", "--host", "0.0.0.0"]) == 2 and "s" not in called
    assert cli.main(["web", "--port", "9000"]) == 0 and called["s"].port == 9000 and called["s"].host == "127.0.0.1"
```
```python
# tests/test_cli.py 에 추가 (run이 필요할 때만 VLM을 켜고 끝나면 끄는지)
def test_run_uses_on_demand_vlm_and_stops(monkeypatch):
    events = []
    class FakeVlm:
        def ensure_ready(self, timeout=180.0, poll=1.0): events.append("ensure")
        def stop(self): events.append("stop")
    captured = {}
    _patch(monkeypatch, [_res()], captured)
    monkeypatch.setattr(cli, "vlm_manager", lambda url: FakeVlm())
    orig = cli.run_batch
    def batch(*a, **k):
        k["on_vlm_needed"](); return orig(*a, **k)
    monkeypatch.setattr(cli, "run_batch", batch)
    assert cli.main(["run"]) == 0 and events == ["ensure", "stop"]
```
`_patch`의 `fake_batch` 시그니처에 `**kwargs`를 추가한다. `tests/test_skill_doc.py`에 `"receipt-evidence web"`이 SKILL.md·README에 있는지 추가.
- [ ] **Step 2: 실패 확인** — `SystemExit`/`AssertionError`
- [ ] **Step 3: 구현 요점** — `web` 서브파서를 추가한다. 호스트가 루프백이 아니면 오류 메시지와 exit 2. `_serve_web`는 `uvicorn.run(create_app(settings), host=settings.host, port=settings.port)`. README에 "웹앱 실행" 절, SKILL에 "웹으로 하려면 `uv run receipt-evidence web` 후 http://127.0.0.1:8765" 추가.
- [ ] **Step 4: 통과 확인** — `uv run pytest -q` → 전체 passed
- [ ] **Step 5: 커밋** — `feat: receipt-evidence web 명령과 문서`

---

### Task 26: 실제 연동 E2E · 화면 확인
**Files:** Create `tests/integration/test_web_e2e.py`
- [ ] **Step 1: 테스트** — 실제 `default_deps` 계열(실제 VLM·MCP)과 스레드 JobManager로 TestClient 흐름을 검증한다. llama-server는 미리 켜지 않는다(추출 작업이 자동으로 켜고 끝나면 꺼지는지까지 확인): 새 정산(실제 영수증 3건) → extract(작업 완료까지 `/job` 폴링) → 읽은 값 3건 → review(인정 196,400·확인필요) → 숙박 수정(`service_date`·`region`) → review 296,400 → finalize(완료까지 폴링) → result(v1, 검증 통과, preview 200, 다운로드 크기 > 100KB). 모델 파일·`llama-server`가 없으면 skip. 끝난 뒤 `/health` 무응답(서버 종료)을 확인한다.
- [ ] **Step 2: 실행** — `uv run pytest -m integration tests/integration/test_web_e2e.py` → `1 passed`
- [ ] **Step 3: 화면 확인** — `uv run receipt-evidence web --data <임시 복사본> --out <임시>`를 백그라운드로 띄우고, 브라우저로 홈·4화면을 열어 C안 시안과 비교한다(레이아웃 깨짐·잘림·한글 폰트 폴백). 문제는 CSS·템플릿에서 고친다. 확인이 끝나면 웹 서버를 끈다(llama-server도 꺼져 있어야 함).
- [ ] **Step 4: 커밋** — `test: 웹앱 실제 연동 E2E`

---

## 자체 점검
- spec 목표 1~5 ↔ Task 21(1)·22(2)·23(3)·24(4)·20/24(5). 보안 ↔ Task 18(경로·업로드)·20(Host·Origin)·24(CSP)·25(루프백). 오류 처리 표 ↔ Task 21(파일 없음)·22(VLM 꺼짐·stale)·23(법령 캐시)·24(문서 실패·미리보기 실패).
- 이름 일관성: `TripService` 메서드명(18)을 라우트(21~24)와 테스트가 같은 이름으로 사용. `do_extract/do_warm_law/do_finalize`(20)를 21·23·24에서 사용. helpers의 `law_from/doc_fake/rail/png_bytes/new_trip/NEW_TRIP_FORM`(17·21).
