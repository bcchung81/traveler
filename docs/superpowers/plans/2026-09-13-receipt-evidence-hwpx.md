# 영수증 증빙서류 HWPX 자동화 Implementation Plan (v2: 다건·일괄·추가 제출)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. (이 프로젝트 사용자는 서브에이전트 위임 없이 메인 세션에서 직접 실행하기를 원한다 → executing-plans로 인라인 실행.)

**Goal:** `data/<출장자>/<출장>/`의 영수증을 로컬 Qwen3-VL로 JSON화한다. korean-law MCP로 공무원 여비 규정을 조회해 규칙엔진으로 지급대상을 판정한다. kordoc MCP로 출장별 증빙내역서 HWPX를 버전 단위로 생성·검증한다. 여러 출장자·여러 출장을 한 번에 처리하고, 추가 제출 시 새 영수증만 다시 읽는 CLI 파이프라인과 Claude Code 스킬을 만든다.

**Architecture:** Python 패키지 `receipt_evidence`(src 레이아웃, uv)의 흐름은 workspace(폴더 발견) → 출장별 ingest → extract(영수증 캐시·병렬, llama-server HTTP) → validate+overrides → 배치 교차검사 → law(일자 캐시, mcp stdio) → 출장별 finalize(규칙 레지스트리·교차검사 → report → 붙임 축소 → kordoc 세션 재사용 → fingerprint 버전) → 배치 요약이다. 외부 의존(VLM·MCP)은 Protocol 뒤에 숨기고 Fake를 주입한다.

**Tech Stack:** Python ≥3.12, pydantic ≥2.12, httpx, PyMuPDF(`import pymupdf`), Pillow, PyYAML, mcp SDK 2.x(`MCPServer`, 결과 필드 `is_error`), pytest; llama.cpp `llama-server`(b9740); npm `kordoc` 4.13.1, `korean-law-mcp` 4.13.0.

**Spec:** docs/superpowers/specs/2026-09-13-receipt-evidence-design.md (v2)

## Global Constraints
- 프로젝트 루트: `/Users/bcchung81/Downloads/데모 시연2` (모든 명령은 이 절대경로에서 실행)
- Python `>=3.12` (시스템 3.14.6), 패키지 관리 `uv`, 테스트 `uv run pytest`
- 패키지명 `receipt_evidence`, CLI 엔트리 `receipt-evidence`, 경로 `src/receipt_evidence/`
- git: `main`에는 설계·계획 문서 초기 커밋만, 구현은 `feat/receipt-evidence-core` 브랜치에서 진행
- 입력 계약: `data/<출장자>/[traveler.yaml]`, `data/<출장자>/<YYYY-MM-DD>_<출장지>[_메모]/[trip.yaml, overrides.yaml, *.jpg|*.jpeg|*.png|*.pdf]`
- 출력 계약: `out/.cache/extract/<PROMPT_VERSION>/<sha256>.json`, `out/.cache/law/<YYYY-MM-DD>.json`, `out/<출장자>/<출장>/{latest.json, work/, v<N>/}`, `out/summary-<run_id>.{md,json}`, `out/run-<run_id>.log`
- 버전 상수: `PROMPT_VERSION = "p1"`(cache.py), `RULES_VERSION = "r1"`(rules.py) — 프롬프트·규칙을 바꾸면 올린다
- VLM 모델: `/Users/bcchung81/.cache/huggingface/hub/models--Qwen--Qwen3-VL-8B-Instruct-GGUF/snapshots/f982a07559d4a2f6c8744d840bf6fccab30eea96/Qwen3VL-8B-Instruct-Q4_K_M.gguf`, mmproj 동일 폴더 `mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf`
- llama-server 포트 `8088`, 호스트 `127.0.0.1`, alias `qwen3-vl`, `temperature 0`, 병렬 슬롯 `VLM_PARALLEL`(기본 1) = CLI `--workers`
- MCP 실행: korean-law = `npx -y korean-law-mcp` (env `LAW_OC=kca-api`), kordoc = `npx -y kordoc mcp`. 배치에서는 `with` 세션으로 1회만 기동
- 법령: 「공무원 여비 규정」 검색어 `공무원 여비 규정`, 조문 `제12조·제13조·제16조·제18조`, 별표 `1·2`. `[현행]` 표기 없는 결과는 사용 금지
- 개인정보: 이미지·전사문은 로컬 전용. 로그에 전사문·카드번호·이메일 기록 금지. 16자리 연속 숫자는 마스킹. 보고서에 카드번호 미기재
- `.gitignore`: `out/`, `data/`, `.venv/`, `*.hwpx`, `__pycache__/`, `.pytest_cache/`, `*.egg-info/`, `.DS_Store`, `design/receipt-evidence-ui/receipt-evidence-screen.html`
- 단위테스트는 네트워크·VLM·npx 불필요(오프라인 FastMCP echo 서버는 허용). 실제 연동은 `@pytest.mark.integration`(기본 제외)
- 모든 커밋 메시지 끝에 아래 두 줄 추가:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01HpxqzjwWRWXkxfagrcUMWY`
- 실제 `data/` 파일 이동(평면 → `data/정백철/2026-07-09_서울/`)은 Task 16 실행 직전에 사용자 확인 후에만 한다
---

## File Structure

```
pyproject.toml                       프로젝트·의존성·pytest 설정
.gitignore, .python-version, README.md
examples/traveler.yaml               출장자 설정 예시
examples/trip.yaml                   출장 설정 예시
examples/overrides.yaml              사용자 확인값 예시
scripts/start_vlm.sh                 llama-server 기동(VLM_PARALLEL)
scripts/record_fixtures.py           실제 korean-law 응답을 tests/fixtures/law/에 녹화
src/receipt_evidence/__init__.py
src/receipt_evidence/models.py       Pydantic 모델 전부
src/receipt_evidence/ingest.py       출장 폴더 스캔·PDF 렌더·해시·리사이즈 → ReceiptImage
src/receipt_evidence/vlm.py          VlmClient Protocol, LlamaServerClient, FakeVlmClient
src/receipt_evidence/cache.py        ExtractCache(sha256 키, PROMPT_VERSION)
src/receipt_evidence/extract.py      프롬프트·JSON 스키마·2-pass 추출·캐시·병렬 → Receipt
src/receipt_evidence/validate.py     결정론 검증(금액·날짜·사업자번호·승인번호 중복)
src/receipt_evidence/mcp_client.py   StdioToolCaller(1회성/세션 재사용), FakeToolCaller
src/receipt_evidence/law.py          search/annex/text 파싱 → LawSnapshot, 일자 캐시
src/receipt_evidence/workspace.py    폴더 발견, traveler.yaml/trip.yaml/overrides.yaml, 출장 자동 제안
src/receipt_evidence/rules.py        규칙 레지스트리, 교차검사, 정액 행, 합계
src/receipt_evidence/report.py       마크다운(GFM 표) 빌더, 정렬, 자동 제안 표기
src/receipt_evidence/export.py       붙임 축소, kordoc 생성, parse_document 합계 검증
src/receipt_evidence/versioning.py   fingerprint, latest.json, 변경 내역
src/receipt_evidence/pipeline.py     prepare_trip / finalize_trip / run_batch / 요약
src/receipt_evidence/cli.py          argparse CLI
tests/conftest.py                    공통 fixture(법령 녹화본·LawSnapshot·TripConfig)
tests/helpers.py                     ColorVlm(색으로 영수증을 식별하는 가짜 VLM), color_png, spec
tests/fixtures/law/{search_law.txt,annex1.html,annex2.html,jo12.txt,jo13.txt,jo16.txt,jo18.txt}
tests/fixtures/echo_mcp_server.py    오프라인 MCP stdio 테스트 서버(FastMCP)
tests/fixtures/golden/receipts.json  data/ 3건 골든값(개인정보 제외 필드만)
tests/test_*.py, tests/integration/test_mcp_live.py, tests/integration/test_e2e.py
.claude/skills/receipt-evidence/SKILL.md
```

---

### Task 0: 저장소 초기화와 프로젝트 스캐폴딩
**Files:** Create `pyproject.toml`, `.gitignore`, `.python-version`, `src/receipt_evidence/__init__.py`, `tests/test_smoke.py`
**Interfaces:** Produces `receipt_evidence.__version__: str`

- [ ] **Step 1: main에 문서 초기 커밋 후 기능 브랜치 생성**
```bash
cd "/Users/bcchung81/Downloads/데모 시연2" && git init -b main && git add docs design/receipt-evidence-ui/*.dc.html design/receipt-evidence-ui/canvas.json && git commit -m "docs: 영수증 증빙 HWPX 자동화 설계·계획과 화면 시안

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HpxqzjwWRWXkxfagrcUMWY" && git switch -c feat/receipt-evidence-core
```
기대: `Switched to a new branch 'feat/receipt-evidence-core'`
- [ ] **Step 2: 실패하는 테스트 작성**
```python
# tests/test_smoke.py
def test_version():
    import receipt_evidence
    assert receipt_evidence.__version__ == "0.1.0"
```
- [ ] **Step 3: uv 초기화 후 실패 확인**
```bash
cd "/Users/bcchung81/Downloads/데모 시연2" && uv init --package --name receipt-evidence . && rm -f src/receipt_evidence/__init__.py
```
`pyproject.toml`을 아래로 덮어쓴다:
```toml
[project]
name = "receipt-evidence"
version = "0.1.0"
description = "출장여비 영수증 증빙서류 HWPX 자동화"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.12", "httpx>=0.28", "pymupdf>=1.26", "pillow>=11", "pyyaml>=6", "mcp>=2.2,<3"]

[project.scripts]
receipt-evidence = "receipt_evidence.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/receipt_evidence"]

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["integration: 실제 llama-server/MCP/네트워크 필요"]
addopts = "-m 'not integration'"
```
`.gitignore`:
```
out/
data/
.venv/
*.hwpx
__pycache__/
.pytest_cache/
*.egg-info/
.DS_Store
design/receipt-evidence-ui/receipt-evidence-screen.html
```
`.python-version`: `3.14`
```bash
uv sync && uv run pytest tests/test_smoke.py
```
주의: `__init__.py`가 없는 상태로 첫 `uv sync`가 편집 설치를 빌드하면 `.pth`가 빠진 채 캐시된다 → Step 4 뒤에는 반드시 `uv sync --reinstall-package receipt-evidence`.
기대: `ModuleNotFoundError: No module named 'receipt_evidence'` 또는 `AttributeError: module 'receipt_evidence' has no attribute '__version__'`
- [ ] **Step 4: 최소 구현**
```python
# src/receipt_evidence/__init__.py
__version__ = "0.1.0"
```
- [ ] **Step 5: 통과 확인** — `uv sync --reinstall-package receipt-evidence && uv run pytest tests/test_smoke.py` → `1 passed`
- [ ] **Step 6: 커밋** — `git add pyproject.toml .gitignore .python-version uv.lock README.md src/receipt_evidence/__init__.py tests/test_smoke.py && git commit -m "chore: uv 프로젝트 스캐폴딩"` (트레일러 2줄 포함)

---

### Task 1: 데이터 모델
**Files:** Create `src/receipt_evidence/models.py`, `tests/test_models.py`
**Interfaces:** Produces `Category, ReceiptImage, Receipt(sha256 포함), Grade, RateTable, LawSnapshot, TravelerProfile, TripConfig(.days, trip_id, proposed, proposal_basis), Verdict, Decision, PipelineResult(traveler, trip_id, version, skipped, changes_md_path, verify_ok, cache_hits, cache_misses, error), BatchResult`

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/test_models.py
from datetime import date
from receipt_evidence.models import (Category, Decision, PipelineResult, RateTable, Receipt, TravelerProfile, TripConfig, Verdict)

def test_receipt_defaults():
    r = Receipt(receipt_id="r1", image_id="i1")
    assert r.category is Category.UNKNOWN and r.amount is None and r.warnings == [] and r.confidence == 1.0 and r.sha256 == ""

def test_trip_days():
    t = TripConfig(traveler_name="정백철", start_date=date(2026, 7, 9), end_date=date(2026, 7, 10))
    assert t.days == 2
    assert TripConfig(traveler_name="x").days is None

def test_rate_table_roundtrip():
    rt = RateTable(grade="제2호", rail="실비(일반실)", ship="실비(2등급)", air="실비", car="실비",
                   daily_allowance=25000, lodging="실비", lodging_caps={"서울특별시": 100000}, meal_allowance=25000)
    assert RateTable.model_validate_json(rt.model_dump_json()) == rt

def test_decision_verdict_value():
    d = Decision(receipt_id=None, item="일비", claimed_amount=0, approved_amount=50000, verdict=Verdict.PAY, basis=[], reasons=[])
    assert d.model_dump()["verdict"] == "지급"

def test_traveler_and_result_defaults():
    p = TravelerProfile(name="정백철")
    assert p.grade is None and p.approval == []
    t = TripConfig(traveler_name="정백철", trip_id="2026-07-09_서울")
    assert t.proposed is False and t.proposal_basis == []
    r = PipelineResult(run_id="r", trip=t, law_mst="1", law_effective=date(2026, 7, 1), receipts=[], decisions=[],
                       totals={}, review_items=[], report_md_path="")
    assert r.skipped is False and r.version is None and r.error is None and r.cache_hits == 0
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_models.py` → `ModuleNotFoundError: No module named 'receipt_evidence.models'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/models.py
from __future__ import annotations
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field

class Category(str, Enum):
    RAIL = "철도"; BUS = "버스"; AIR = "항공"; TAXI = "택시"
    LODGING = "숙박"; MEAL = "식사"; OTHER = "기타"; UNKNOWN = "미상"

class ReceiptImage(BaseModel):
    image_id: str
    source_path: str
    page: int = 1
    png_path: str
    sha256: str
    width: int
    height: int

class Receipt(BaseModel):
    receipt_id: str
    image_id: str
    sha256: str = ""
    category: Category = Category.UNKNOWN
    merchant: str | None = None
    business_no: str | None = None
    amount: int | None = None
    paid_at: datetime | None = None
    service_date: date | None = None
    service_end_date: date | None = None
    origin: str | None = None
    destination: str | None = None
    seat_class: str | None = None
    train_no: str | None = None
    approval_no: str | None = None
    card_masked: str | None = None
    payer_name: str | None = None
    region: str | None = None
    nights: int | None = None
    transcript_path: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    confidence: float = 1.0

Grade = Literal["제1호", "제2호"]

class RateTable(BaseModel):
    grade: Grade
    rail: str
    ship: str
    air: str
    car: str
    daily_allowance: int
    lodging: str
    lodging_caps: dict[str, int] | None = None
    meal_allowance: int

class LawSnapshot(BaseModel):
    law_name: str
    law_id: str
    mst: str
    promulgated: date
    effective: date
    fetched_at: datetime
    annexes: dict[str, str] = Field(default_factory=dict)
    articles: dict[str, str] = Field(default_factory=dict)
    rate_tables: dict[str, RateTable] = Field(default_factory=dict)

class TravelerProfile(BaseModel):
    name: str
    position: str = ""
    grade: Grade | None = None
    org: str = ""
    dept: str = ""
    workplace_region: str = ""
    approval: list[str] = Field(default_factory=list)

class TripConfig(BaseModel):
    traveler_name: str
    trip_id: str = ""
    position: str = ""
    grade: Grade | None = None
    org: str = ""
    dept: str = ""
    workplace_region: str = ""
    destination_region: str = ""
    start_date: date | None = None
    end_date: date | None = None
    purpose: str = ""
    route_stations: list[str] = Field(default_factory=list)
    lodging_region: str | None = None
    over_cap_reason: str | None = None
    taxi_reason: str | None = None
    official_vehicle: bool = False
    within_workplace: bool = False
    duration_hours: float | None = None
    approval: list[str] = Field(default_factory=list)
    template_fields: dict[str, str] = Field(default_factory=dict)
    proposed: bool = False
    proposal_basis: list[str] = Field(default_factory=list)

    @property
    def days(self) -> int | None:
        if self.start_date is None or self.end_date is None:
            return None
        return (self.end_date - self.start_date).days + 1

class Verdict(str, Enum):
    PAY = "지급"; REDUCED = "감액지급"; DENIED = "불인정"; REVIEW = "확인필요"

class Decision(BaseModel):
    receipt_id: str | None
    item: str
    claimed_amount: int
    approved_amount: int
    verdict: Verdict
    basis: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

class PipelineResult(BaseModel):
    run_id: str
    traveler: str = ""
    trip_id: str = ""
    trip: TripConfig
    law_mst: str
    law_effective: date
    receipts: list[Receipt]
    decisions: list[Decision]
    totals: dict[str, int]
    review_items: list[str]
    report_md_path: str
    hwpx_path: str | None = None
    version: int | None = None
    skipped: bool = False
    changes_md_path: str | None = None
    verify_ok: bool | None = None
    cache_hits: int = 0
    cache_misses: int = 0
    error: str | None = None

class BatchResult(BaseModel):
    run_id: str
    results: list[PipelineResult]
    warnings: list[str] = Field(default_factory=list)
    summary_md_path: str
    summary_json_path: str
```
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_models.py` → `5 passed`
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/models.py tests/test_models.py && git commit -m "feat: Pydantic 데이터 모델(출장자·버전·배치 결과 포함)"`

---

### Task 2: ingest (출장 폴더 스캔·렌더·해시)
**Files:** Create `src/receipt_evidence/ingest.py`, `tests/test_ingest.py`
**Interfaces:** Consumes `ReceiptImage`. Produces `SUPPORTED: set[str]`, `ingest(data_dir: Path, out_dir: Path) -> list[ReceiptImage]` (data_dir = 출장 폴더 1개, 파이프라인은 `out/<출장자>/<출장>/work`를 out_dir로 넘김), `sha256_file(path) -> str`, `is_blank(png: Path) -> bool`, `normalize_image(src: Path, dst: Path, max_side: int = 2048) -> tuple[int,int]`, `render_pdf(pdf: Path, out_dir: Path, dpi: int = 200) -> list[Path]`

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/test_ingest.py
import json
import pymupdf
from pathlib import Path
from PIL import Image, ImageDraw
from receipt_evidence.ingest import ingest, is_blank, normalize_image, render_pdf, sha256_file

def _jpg(path: Path, size=(1440, 3088)):
    img = Image.new("RGB", size, "white"); ImageDraw.Draw(img).text((100, 100), "48,200", fill="black"); img.save(path, "JPEG")

def _pdf(path: Path):
    doc = pymupdf.open(); p = doc.new_page(); p.insert_text((72, 72), "100,000 won"); doc.new_page(); doc.save(path)

def test_normalize_resizes_long_side(tmp_path):
    _jpg(tmp_path / "a.jpg"); w, h = normalize_image(tmp_path / "a.jpg", tmp_path / "a.png")
    assert h == 2048 and w == 955

def test_render_pdf_drops_blank_page(tmp_path):
    _pdf(tmp_path / "r.pdf"); pages = render_pdf(tmp_path / "r.pdf", tmp_path / "pages")
    assert len(pages) == 1 and pages[0].name.endswith("-p1.png")

def test_is_blank(tmp_path):
    Image.new("RGB", (100, 100), "white").save(tmp_path / "b.png"); assert is_blank(tmp_path / "b.png")

def test_ingest_dedups_and_writes_manifest(tmp_path):
    d = tmp_path / "data"; d.mkdir(); _jpg(d / "x.jpg"); (d / "x_copy.jpg").write_bytes((d / "x.jpg").read_bytes()); _pdf(d / "r.pdf")
    imgs = ingest(d, tmp_path / "out")
    assert len(imgs) == 2 and (tmp_path / "out" / "manifest.json").exists()  # r.pdf 1쪽(2쪽은 빈 페이지) + x.jpg (x_copy.jpg는 중복)
    jpg = next(i for i in imgs if i.source_path.endswith("x.jpg"))
    assert jpg.sha256 == sha256_file(d / "x.jpg") and jpg.image_id == jpg.sha256[:12] + "-p1"
    assert [m["image_id"] for m in json.loads((tmp_path / "out" / "manifest.json").read_text())] == [i.image_id for i in imgs]
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_ingest.py` → `ModuleNotFoundError: No module named 'receipt_evidence.ingest'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/ingest.py
from __future__ import annotations
import hashlib, json
from pathlib import Path
import pymupdf
from PIL import Image
from .models import ReceiptImage

SUPPORTED = {".jpg", ".jpeg", ".png", ".pdf"}

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def is_blank(png: Path, dark_ratio: float = 0.0001) -> bool:
    # 실측(200dpi): 텍스트 한 줄 페이지 0.00035, 토스 메일 푸터 페이지 0.00345, 빈 페이지 0.0
    # 푸터처럼 내용이 있는 쪽은 남기고, extract 단계에서 같은 원본 파일의 쪽을 영수증 1건으로 묶는다
    img = Image.open(png).convert("L")
    hist = img.histogram()
    dark = sum(hist[:200])
    return dark / (img.width * img.height) < dark_ratio

def normalize_image(src: Path, dst: Path, max_side: int = 2048) -> tuple[int, int]:
    img = Image.open(src).convert("RGB")
    scale = min(1.0, max_side / max(img.size))
    if scale < 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "PNG")
    return img.width, img.height

def render_pdf(pdf: Path, out_dir: Path, dpi: int = 200) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = sha256_file(pdf)[:12]
    paths: list[Path] = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, start=1):
            p = out_dir / f"{stem}-p{i}.png"
            page.get_pixmap(dpi=dpi).save(p)
            if is_blank(p):
                p.unlink()
                continue
            paths.append(p)
    return paths

def ingest(data_dir: Path, out_dir: Path) -> list[ReceiptImage]:
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    result: list[ReceiptImage] = []
    for src in sorted(p for p in data_dir.iterdir() if p.suffix.lower() in SUPPORTED and not p.name.startswith(".")):
        sha = sha256_file(src)
        if sha in seen:
            continue
        seen.add(sha)
        if src.suffix.lower() == ".pdf":
            rendered = render_pdf(src, out_dir / "_pdf_pages")
            for p in rendered:
                page = int(p.stem.rsplit("-p", 1)[1])
                dst = img_dir / f"{sha[:12]}-p{page}.png"
                w, h = normalize_image(p, dst)
                result.append(ReceiptImage(image_id=dst.stem, source_path=str(src), page=page, png_path=str(dst), sha256=sha, width=w, height=h))
        else:
            dst = img_dir / f"{sha[:12]}-p1.png"
            w, h = normalize_image(src, dst)
            result.append(ReceiptImage(image_id=dst.stem, source_path=str(src), page=1, png_path=str(dst), sha256=sha, width=w, height=h))
    (out_dir / "manifest.json").write_text(json.dumps([r.model_dump() for r in result], ensure_ascii=False, indent=2), encoding="utf-8")
    return result
```
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_ingest.py` → `4 passed`
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/ingest.py tests/test_ingest.py && git commit -m "feat: ingest 단계(스캔·PDF 렌더·해시·리사이즈)"`

---

### Task 3: VLM 클라이언트 (llama-server) + Fake
**Files:** Create `src/receipt_evidence/vlm.py`, `tests/test_vlm.py`, `scripts/start_vlm.sh`
**Interfaces:** Produces `VlmClient` Protocol (`chat(messages: list[dict], *, json_schema: dict | None = None, max_tokens: int = 2048, schema_mode: str = "json_schema") -> str`, `healthy() -> bool`), `LlamaServerClient(base_url, timeout, transport)`, `image_content(png_path: Path) -> dict`, `FakeVlmClient(replies: list[str])` with `.calls`

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/test_vlm.py
import json, httpx
from pathlib import Path
from PIL import Image
from receipt_evidence.vlm import LlamaServerClient, FakeVlmClient, image_content

def test_chat_sends_json_schema_and_temperature_zero():
    seen = {}
    def handler(req: httpx.Request):
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"a\":1}"}}]})
    c = LlamaServerClient("http://vlm", transport=httpx.MockTransport(handler))
    assert c.healthy()
    out = c.chat([{"role": "user", "content": "hi"}], json_schema={"type": "object"})
    assert out == '{"a":1}' and seen["temperature"] == 0
    assert seen["response_format"] == {"type": "json_schema", "json_schema": {"name": "receipt", "schema": {"type": "object"}}}

def test_chat_json_object_mode():
    seen = {}
    def handler(req):
        seen.update(json.loads(req.content)); return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    c = LlamaServerClient("http://vlm", transport=httpx.MockTransport(handler))
    c.chat([], json_schema={"type": "object"}, schema_mode="json_object")
    assert seen["response_format"] == {"type": "json_object", "schema": {"type": "object"}}

def test_image_content_base64(tmp_path):
    p = tmp_path / "a.png"; Image.new("RGB", (4, 4)).save(p)
    ic = image_content(p)
    assert ic["type"] == "image_url" and ic["image_url"]["url"].startswith("data:image/png;base64,")

def test_fake_client_records_calls():
    f = FakeVlmClient(["one", "two"])
    assert f.chat([{"role": "user", "content": "x"}]) == "one" and f.chat([]) == "two" and len(f.calls) == 2
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_vlm.py` → `ModuleNotFoundError: No module named 'receipt_evidence.vlm'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/vlm.py
from __future__ import annotations
import base64
from pathlib import Path
from typing import Protocol
import httpx

class VlmClient(Protocol):
    def chat(self, messages: list[dict], *, json_schema: dict | None = None, max_tokens: int = 2048, schema_mode: str = "json_schema") -> str: ...
    def healthy(self) -> bool: ...

def image_content(png_path: Path) -> dict:
    b64 = base64.b64encode(Path(png_path).read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}

class LlamaServerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8088", timeout: float = 600.0, transport: httpx.BaseTransport | None = None, model: str = "qwen3-vl"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def healthy(self) -> bool:
        try:
            return self._http.get("/health").json().get("status") == "ok"
        except (httpx.HTTPError, ValueError):
            return False

    def chat(self, messages: list[dict], *, json_schema: dict | None = None, max_tokens: int = 2048, schema_mode: str = "json_schema") -> str:
        body: dict = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
        if json_schema is not None:
            if schema_mode == "json_object":
                body["response_format"] = {"type": "json_object", "schema": json_schema}
            else:
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "receipt", "schema": json_schema}}
        last: Exception | None = None
        for _ in range(2):
            try:
                r = self._http.post("/v1/chat/completions", json=body)
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            except httpx.TimeoutException as e:
                last = e
        raise RuntimeError(f"llama-server 응답 없음: {last}")

class FakeVlmClient:
    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls: list[dict] = []

    def healthy(self) -> bool:
        return True

    def chat(self, messages: list[dict], *, json_schema: dict | None = None, max_tokens: int = 2048, schema_mode: str = "json_schema") -> str:
        self.calls.append({"messages": messages, "json_schema": json_schema, "schema_mode": schema_mode})
        return self._replies.pop(0)
```
```bash
# scripts/start_vlm.sh
#!/usr/bin/env bash
set -euo pipefail
SNAP="$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-VL-8B-Instruct-GGUF/snapshots/f982a07559d4a2f6c8744d840bf6fccab30eea96"
MODEL="${VLM_MODEL:-$SNAP/Qwen3VL-8B-Instruct-Q4_K_M.gguf}"
MMPROJ="${VLM_MMPROJ:-$SNAP/mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf}"
PORT="${VLM_PORT:-8088}"
PARALLEL="${VLM_PARALLEL:-1}"          # receipt-evidence run --workers 와 같은 값
CTX="${VLM_CTX:-$((12288 * PARALLEL))}"  # 슬롯당 약 12k 토큰
mkdir -p out
exec llama-server -m "$MODEL" --mmproj "$MMPROJ" --host 127.0.0.1 --port "$PORT" \
  -c "$CTX" -ngl 99 -np "$PARALLEL" --jinja --temp 0 --alias qwen3-vl --no-warmup > out/vlm.log 2>&1
```
`chmod +x scripts/start_vlm.sh`
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_vlm.py` → `4 passed`; `bash -n scripts/start_vlm.sh` → 출력 없음
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/vlm.py tests/test_vlm.py scripts/start_vlm.sh && git commit -m "feat: llama-server VLM 클라이언트와 기동 스크립트"`

---

### Task 4: extract (2-pass 전사·구조화 + 영수증 캐시 + 병렬)
**Files:** Create `src/receipt_evidence/cache.py`, `src/receipt_evidence/extract.py`, `tests/test_extract.py`
**Interfaces:** Consumes `VlmClient`, `image_content`, `ReceiptImage`, `Receipt`. Produces:
- `cache.PROMPT_VERSION: str`
- `ExtractCache(root: Path, prompt_version: str = PROMPT_VERSION)`, with `.has(sha: str) -> bool`, `.get(sha) -> dict | None` (hits/misses 집계), `.put(sha, transcript: str, data: dict) -> None`, `.hits: int`, `.misses: int`
- `RECEIPT_SCHEMA: dict`, `TRANSCRIBE_PROMPT: str`, `parse_json_loose(text: str) -> dict`, `parse_date(s) -> date | None`, `parse_datetime(s) -> datetime | None`, `mask_card(s: str | None) -> str | None`, `to_receipt(image_id: str, data: dict, transcript_path: str) -> Receipt`, `group_by_source(images: list[ReceiptImage]) -> list[list[ReceiptImage]]`
- `extract_receipts(vlm: VlmClient, images: list[ReceiptImage], out_dir: Path, *, cache: ExtractCache | None = None, workers: int = 1) -> list[Receipt]`: 원본 파일(sha256) 1개 = 영수증 1건이고, `receipt_id`·`image_id`는 첫 쪽 image_id, `sha256`은 원본 해시다. 결과 순서는 입력 순서를 따른다. 실패(EXTRACT_FAILED)는 캐시에 저장하지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/test_extract.py
import json, threading
from datetime import date, datetime
from pathlib import Path
import httpx
from PIL import Image
from receipt_evidence.cache import ExtractCache
from receipt_evidence.models import ReceiptImage, Category
from receipt_evidence.vlm import FakeVlmClient
from receipt_evidence.extract import extract_receipts, parse_json_loose, parse_date, parse_datetime, mask_card, to_receipt

KTX = {"category": "철도", "merchant": "한국철도공사", "business_no": "314-82-10024", "amount": 48200, "paid_at": "2026.07.08 22:10",
       "service_date": "2026-07-09(목)", "service_end_date": None, "origin": "나주", "destination": "용산", "seat_class": "일반실",
       "train_no": "KTX-산천 424", "approval_no": "55431218", "card_masked": "53618190****574*", "payer_name": None, "region": None, "nights": None}

def _img(tmp_path):
    p = tmp_path / "abc-p1.png"; Image.new("RGB", (8, 8)).save(p)
    return ReceiptImage(image_id="abc-p1", source_path="x.jpg", png_path=str(p), sha256="abc", width=8, height=8)

def test_parse_helpers():
    assert parse_json_loose("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert parse_date("2026-07-09(목)") == date(2026, 7, 9) and parse_date("2026/08/21 16:44:06") == date(2026, 8, 21)
    assert parse_datetime("2026.07.08 22:10") == datetime(2026, 7, 8, 22, 10) and parse_date("없음") is None
    assert parse_date("2026년 07월 09일 (목)") == date(2026, 7, 9)  # 코레일 영수증 원문 표기
    assert parse_datetime("2026년 06월 11일 (목) 08:31") == datetime(2026, 6, 11, 8, 31)
    assert mask_card("5361819012345741") == "536181******41" and mask_card("53618190****574*") == "53618190****574*"

def test_two_pass_and_schema(tmp_path):
    vlm = FakeVlmClient(["전사문: 결제금액 48,200원", json.dumps(KTX, ensure_ascii=False)])
    rs = extract_receipts(vlm, [_img(tmp_path)], tmp_path / "out")
    r = rs[0]
    assert r.category is Category.RAIL and r.amount == 48200 and r.service_date == date(2026, 7, 9) and r.approval_no == "55431218"
    assert r.sha256 == "abc"
    assert vlm.calls[0]["json_schema"] is None and vlm.calls[1]["json_schema"]["type"] == "object"
    assert (tmp_path / "out" / "transcripts" / "abc-p1.txt").read_text(encoding="utf-8").startswith("전사문")
    assert any(c["type"] == "image_url" for c in vlm.calls[1]["messages"][-1]["content"])

def test_fallback_to_json_object_then_loose(tmp_path):
    vlm = FakeVlmClient(["t", "not json", "still not", "```json\n" + json.dumps(KTX) + "\n```"])
    r = extract_receipts(vlm, [_img(tmp_path)], tmp_path / "out")[0]
    assert r.amount == 48200 and vlm.calls[2]["schema_mode"] == "json_object"

def test_extract_failed_marks_unknown(tmp_path):
    vlm = FakeVlmClient(["t", "x", "y", "z"])
    r = extract_receipts(vlm, [_img(tmp_path)], tmp_path / "out")[0]
    assert r.category is Category.UNKNOWN and "EXTRACT_FAILED" in r.warnings

def test_to_receipt_unknown_category():
    r = to_receipt("i", {"category": "이상한값", "amount": "1,000"}, "t.txt")
    assert r.category is Category.UNKNOWN and r.amount == 1000

def test_multipage_pdf_becomes_one_receipt(tmp_path):
    imgs = []
    for page in (1, 2):
        p = tmp_path / f"pdf-p{page}.png"; Image.new("RGB", (8, 8)).save(p)
        imgs.append(ReceiptImage(image_id=f"pdf-p{page}", source_path="stay.pdf", page=page, png_path=str(p), sha256="pdf", width=8, height=8))
    stay = dict(KTX, category="숙박", amount=100000, approval_no="68325420")
    vlm = FakeVlmClient(["결제금액 100,000원", "토스페이먼츠 주식회사", json.dumps(stay, ensure_ascii=False)])
    rs = extract_receipts(vlm, imgs, tmp_path / "out")
    assert len(rs) == 1 and rs[0].receipt_id == "pdf-p1" and rs[0].amount == 100000
    assert sum(c["type"] == "image_url" for c in vlm.calls[2]["messages"][-1]["content"]) == 2
    assert "[2쪽]" in (tmp_path / "out" / "transcripts" / "pdf-p1.txt").read_text(encoding="utf-8")

class _Http400OnJsonSchema(FakeVlmClient):
    """llama-server가 json_schema response_format을 400으로 거부하는 상황 재현"""
    def chat(self, messages, *, json_schema=None, max_tokens=2048, schema_mode="json_schema"):
        if json_schema is not None and schema_mode == "json_schema":
            req = httpx.Request("POST", "http://vlm/v1/chat/completions")
            raise httpx.HTTPStatusError("400", request=req, response=httpx.Response(400, request=req))
        return super().chat(messages, json_schema=json_schema, max_tokens=max_tokens, schema_mode=schema_mode)

def test_http_400_on_json_schema_falls_back_to_json_object(tmp_path):
    vlm = _Http400OnJsonSchema(["t", json.dumps(KTX, ensure_ascii=False)])
    r = extract_receipts(vlm, [_img(tmp_path)], tmp_path / "out")[0]
    assert r.amount == 48200 and vlm.calls[-1]["schema_mode"] == "json_object"

def test_cache_skips_vlm_on_second_run(tmp_path):
    cache = ExtractCache(tmp_path / ".cache")
    vlm = FakeVlmClient(["결제금액 48,200원", json.dumps(KTX, ensure_ascii=False)])
    r1 = extract_receipts(vlm, [_img(tmp_path)], tmp_path / "out1", cache=cache)[0]
    vlm2 = FakeVlmClient([])
    r2 = extract_receipts(vlm2, [_img(tmp_path)], tmp_path / "out2", cache=cache)[0]
    assert r1.amount == r2.amount == 48200 and vlm2.calls == [] and (cache.hits, cache.misses) == (1, 1)
    assert r2.sha256 == "abc" and (tmp_path / "out2" / "transcripts" / "abc-p1.txt").read_text(encoding="utf-8") == "결제금액 48,200원"
    assert (cache.dir / "abc.json").exists() and cache.dir.parent.name == "extract"

def test_failed_extraction_is_not_cached(tmp_path):
    cache = ExtractCache(tmp_path / ".cache")
    extract_receipts(FakeVlmClient(["t", "x", "y", "z"]), [_img(tmp_path)], tmp_path / "out", cache=cache)
    assert not cache.has("abc")

class _ConstantVlm:
    def __init__(self):
        self.calls = 0; self._lock = threading.Lock()
    def healthy(self):
        return True
    def chat(self, messages, *, json_schema=None, max_tokens=2048, schema_mode="json_schema"):
        with self._lock:
            self.calls += 1
        return json.dumps(KTX, ensure_ascii=False) if json_schema else "결제금액 48,200원"

def test_workers_preserve_order(tmp_path):
    imgs = []
    for i in range(6):
        p = tmp_path / f"i{i}-p1.png"; Image.new("RGB", (8, 8)).save(p)
        imgs.append(ReceiptImage(image_id=f"i{i}-p1", source_path=f"{i}.jpg", png_path=str(p), sha256=f"s{i}", width=8, height=8))
    vlm = _ConstantVlm()
    rs = extract_receipts(vlm, imgs, tmp_path / "out", workers=4)
    assert [r.receipt_id for r in rs] == [f"i{i}-p1" for i in range(6)] and vlm.calls == 12
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_extract.py` → `ModuleNotFoundError: No module named 'receipt_evidence.cache'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/cache.py
from __future__ import annotations
import json, threading
from pathlib import Path

PROMPT_VERSION = "p1"  # extract.py 프롬프트나 스키마를 바꾸면 올린다 → 이전 캐시가 자동으로 무효화됨

class ExtractCache:
    """원본 영수증 sha256 → {transcript, data}. 추가 제출·재실행 시 새 영수증만 VLM으로 읽기 위한 캐시."""

    def __init__(self, root: Path, prompt_version: str = PROMPT_VERSION):
        self.dir = Path(root) / "extract" / prompt_version
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()

    def _path(self, sha: str) -> Path:
        return self.dir / f"{sha}.json"

    def has(self, sha: str) -> bool:
        return self._path(sha).exists()

    def get(self, sha: str) -> dict | None:
        p = self._path(sha)
        with self._lock:
            if not p.exists():
                self.misses += 1
                return None
            self.hits += 1
        return json.loads(p.read_text(encoding="utf-8"))

    def put(self, sha: str, transcript: str, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(sha).with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"transcript": transcript, "data": data}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path(sha))
```
```python
# src/receipt_evidence/extract.py
from __future__ import annotations
import json, re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Any
import httpx
from .cache import ExtractCache
from .models import Category, Receipt, ReceiptImage
from .vlm import VlmClient, image_content

_STR = {"type": ["string", "null"]}
RECEIPT_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "category": {"type": "string", "enum": [c.value for c in Category]},
        "merchant": _STR, "business_no": _STR, "amount": {"type": ["integer", "null"]},
        "paid_at": _STR, "service_date": _STR, "service_end_date": _STR,
        "origin": _STR, "destination": _STR, "seat_class": _STR, "train_no": _STR,
        "approval_no": _STR, "card_masked": _STR, "payer_name": _STR, "region": _STR,
        "nights": {"type": ["integer", "null"]},
    },
    "required": ["category", "merchant", "business_no", "amount", "paid_at", "service_date", "service_end_date",
                 "origin", "destination", "seat_class", "train_no", "approval_no", "card_masked", "payer_name", "region", "nights"],
}
TRANSCRIBE_PROMPT = "이 영수증 이미지에 보이는 모든 텍스트를 위에서 아래로, 왼쪽에서 오른쪽으로 빠짐없이 전사하세요. 숫자·날짜·번호는 원문 그대로 적고 해석이나 요약을 하지 마세요."
STRUCTURE_PROMPT = (
    "아래 전사문과 이미지를 근거로 영수증 정보를 JSON으로 추출하세요. 규칙: (1) 전사문에 없는 값은 null. "
    "(2) category는 철도(KTX·SRT·기차)/버스/항공/택시/숙박(호텔·모텔·숙소 예약)/식사/기타/미상 중 하나. "
    "(3) amount는 최종 결제금액을 원 단위 정수로. (4) paid_at은 승인·결제 일시, service_date는 운행일 또는 체크인일, "
    "service_end_date는 체크아웃일. (5) origin/destination은 출발·도착역, seat_class는 일반실/특실, train_no는 열차 종류와 번호. "
    "(6) card_masked는 표기된 마스킹 그대로. (7) region은 숙박지 시·도명(모르면 null). nights는 숙박 밤 수. "
    "(8) 날짜는 YYYY-MM-DD, 일시는 YYYY-MM-DD HH:MM 형식으로 쓰세요.\n\n[전사문]\n"
)
# 2026-07-09 / 2026.07.08 22:10 / 2026/08/21 16:44:06 / 2026년 06월 11일 (목) 08:31 모두 허용
_DT = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?(?:[^\d]{0,6}?(\d{1,2}):(\d{2})(?::(\d{2}))?)?")

def parse_datetime(s: Any) -> datetime | None:
    if not isinstance(s, str):
        return None
    m = _DT.search(s)
    if not m:
        return None
    y, mo, d, hh, mm, ss = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
    except ValueError:
        return None

def parse_date(s: Any) -> date | None:
    dt = parse_datetime(s)
    return dt.date() if dt else None

def mask_card(s: str | None) -> str | None:
    if s is None:
        return None
    return re.sub(r"(\d{6})\d{8,10}(\d{2})", lambda m: m.group(1) + "*" * 6 + m.group(2), s)

def parse_json_loose(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a < 0 or b <= a:
            raise
        return json.loads(t[a:b + 1])

def _int(v: Any) -> int | None:
    if v is None:
        return None
    digits = re.sub(r"[^\d]", "", str(v))
    return int(digits) if digits else None

def to_receipt(image_id: str, data: dict, transcript_path: str) -> Receipt:
    try:
        cat = Category(data.get("category"))
    except ValueError:
        cat = Category.UNKNOWN
    s = lambda k: (str(data[k]).strip() or None) if data.get(k) not in (None, "") else None
    return Receipt(
        receipt_id=image_id, image_id=image_id, category=cat, merchant=s("merchant"), business_no=s("business_no"),
        amount=_int(data.get("amount")), paid_at=parse_datetime(data.get("paid_at")), service_date=parse_date(data.get("service_date")),
        service_end_date=parse_date(data.get("service_end_date")), origin=s("origin"), destination=s("destination"),
        seat_class=s("seat_class"), train_no=s("train_no"), approval_no=s("approval_no"), card_masked=mask_card(s("card_masked")),
        payer_name=s("payer_name"), region=s("region"), nights=_int(data.get("nights")), transcript_path=transcript_path, raw=data)

def _structure(vlm: VlmClient, pngs: list[Path], transcript: str) -> dict | None:
    content = [{"type": "text", "text": STRUCTURE_PROMPT + transcript}] + [image_content(p) for p in pngs]
    user = {"role": "user", "content": content}
    for mode in ("json_schema", "json_object", "json_schema"):
        try:
            text = vlm.chat([user], json_schema=RECEIPT_SCHEMA, max_tokens=1024, schema_mode=mode)
            return parse_json_loose(text)
        except (json.JSONDecodeError, ValueError, httpx.HTTPStatusError):  # 400(스키마 미지원)도 다음 모드로 폴백
            continue
    return None

def group_by_source(images: list[ReceiptImage]) -> list[list[ReceiptImage]]:
    groups: dict[str, list[ReceiptImage]] = {}
    for img in images:
        groups.setdefault(img.sha256, []).append(img)
    return [sorted(g, key=lambda i: i.page) for g in groups.values()]

def _extract_group(vlm: VlmClient, group: list[ReceiptImage], tdir: Path, cache: ExtractCache | None) -> Receipt:
    first = group[0]
    tpath = tdir / f"{first.image_id}.txt"
    cached = cache.get(first.sha256) if cache is not None else None
    if cached is not None:
        transcript, data = cached["transcript"], cached["data"]
    else:
        pages = []
        for img in group:
            text = vlm.chat([{"role": "user", "content": [{"type": "text", "text": TRANSCRIBE_PROMPT}, image_content(Path(img.png_path))]}], max_tokens=2048)
            pages.append(text if len(group) == 1 else f"[{img.page}쪽]\n{text}")
        transcript = "\n\n".join(pages)
        data = _structure(vlm, [Path(i.png_path) for i in group], transcript)
        if cache is not None and data is not None:
            cache.put(first.sha256, transcript, data)
    tpath.write_text(transcript, encoding="utf-8")
    if data is None:
        return Receipt(receipt_id=first.image_id, image_id=first.image_id, sha256=first.sha256, transcript_path=str(tpath),
                       warnings=["EXTRACT_FAILED"], confidence=0.0)
    return to_receipt(first.image_id, data, str(tpath)).model_copy(update={"sha256": first.sha256})

def extract_receipts(vlm: VlmClient, images: list[ReceiptImage], out_dir: Path, *, cache: ExtractCache | None = None,
                     workers: int = 1) -> list[Receipt]:
    tdir = out_dir / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    groups = group_by_source(images)
    if workers <= 1:
        receipts = [_extract_group(vlm, g, tdir, cache) for g in groups]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            receipts = list(ex.map(lambda g: _extract_group(vlm, g, tdir, cache), groups))
    (out_dir / "receipts.json").write_text(json.dumps([r.model_dump(mode="json") for r in receipts], ensure_ascii=False, indent=2), encoding="utf-8")
    return receipts
```
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_extract.py` → `10 passed`
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/cache.py src/receipt_evidence/extract.py tests/test_extract.py && git commit -m "feat: Qwen3-VL 2-pass 추출에 영수증 캐시와 병렬 처리 추가"`

---

### Task 5: validate (결정론 검증)
**Files:** Create `src/receipt_evidence/validate.py`, `tests/test_validate.py`
**Interfaces:** Consumes `Receipt`. Produces `ERROR_CODES: frozenset[str]`, `bizno_valid(s: str) -> bool`, `validate_receipt(r: Receipt, transcript: str) -> Receipt`, `validate_all(receipts: list[Receipt], transcripts: dict[str, str]) -> list[Receipt]`

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/test_validate.py
from datetime import date
from receipt_evidence.models import Receipt, Category
from receipt_evidence.validate import bizno_valid, validate_receipt, validate_all, ERROR_CODES

def _r(**kw):
    base = dict(receipt_id="a", image_id="a", category=Category.RAIL, amount=48200, business_no="314-82-10024", approval_no="55431218", service_date=date(2026, 7, 9))
    base.update(kw); return Receipt(**base)

def test_bizno_checksum():
    assert bizno_valid("314-82-10024") and not bizno_valid("314-82-10025") and not bizno_valid("12")

def test_clean_receipt_has_no_warnings():
    r = validate_receipt(_r(), "결제금액 48,200원 승인번호 55431218 사업자 314-82-10024")
    assert r.warnings == [] and r.confidence == 1.0

def test_amount_not_in_transcript():
    r = validate_receipt(_r(), "결제금액 48,000원 승인번호 55431218")
    assert "AMOUNT_NOT_IN_TRANSCRIPT" in r.warnings and r.confidence == 0.8

def test_missing_fields_and_bad_bizno():
    r = validate_receipt(_r(amount=None, business_no="111-11-11111", approval_no=None, service_date=None, paid_at=None), "")
    assert {"MISSING_AMOUNT", "BIZNO_CHECKSUM", "MISSING_APPROVAL", "MISSING_DATE"} <= set(r.warnings)
    assert r.confidence == 0.4  # error 3건(MISSING_AMOUNT, BIZNO_CHECKSUM, MISSING_DATE) × 0.2

def test_duplicate_approval_across_receipts():
    rs = validate_all([_r(receipt_id="a"), _r(receipt_id="b", image_id="b")], {"a": "48,200 55431218 314-82-10024", "b": "48,200 55431218 314-82-10024"})
    assert all("DUP_APPROVAL" in r.warnings for r in rs) and "DUP_APPROVAL" in ERROR_CODES
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_validate.py` → `ModuleNotFoundError: No module named 'receipt_evidence.validate'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/validate.py
from __future__ import annotations
import re
from collections import Counter
from .models import Receipt

ERROR_CODES = frozenset({"MISSING_AMOUNT", "AMOUNT_NOT_IN_TRANSCRIPT", "BIZNO_CHECKSUM", "DUP_APPROVAL", "EXTRACT_FAILED", "MISSING_DATE"})
_W = [1, 3, 7, 1, 3, 7, 1, 3, 5]

def bizno_valid(s: str) -> bool:
    d = re.sub(r"\D", "", s or "")
    if len(d) != 10:
        return False
    n = [int(c) for c in d]
    total = sum(a * b for a, b in zip(n[:9], _W)) + (n[8] * 5) // 10
    return (10 - total % 10) % 10 == n[9]

def _confidence(warnings: list[str]) -> float:
    errors = sum(1 for w in warnings if w in ERROR_CODES)
    return round(max(0.0, 1.0 - 0.2 * errors), 2)

def validate_receipt(r: Receipt, transcript: str) -> Receipt:
    w = [x for x in r.warnings if x != "DUP_APPROVAL"]
    flat = re.sub(r"\s", "", transcript)
    if r.amount is None:
        w.append("MISSING_AMOUNT")
    elif f"{r.amount:,}" not in flat and str(r.amount) not in flat:
        w.append("AMOUNT_NOT_IN_TRANSCRIPT")
    if r.business_no is None:
        w.append("MISSING_BIZNO")
    elif not bizno_valid(r.business_no):
        w.append("BIZNO_CHECKSUM")
    if r.approval_no is None:
        w.append("MISSING_APPROVAL")
    elif re.sub(r"\D", "", r.approval_no) not in flat:
        w.append("APPROVAL_NOT_IN_TRANSCRIPT")
    if r.service_date is None and r.paid_at is None:
        w.append("MISSING_DATE")
    return r.model_copy(update={"warnings": w, "confidence": _confidence(w)})

def validate_all(receipts: list[Receipt], transcripts: dict[str, str]) -> list[Receipt]:
    out = [validate_receipt(r, transcripts.get(r.receipt_id, "")) for r in receipts]
    dup = {k for k, c in Counter(r.approval_no for r in out if r.approval_no).items() if c > 1}
    final = []
    for r in out:
        w = r.warnings + (["DUP_APPROVAL"] if r.approval_no in dup else [])
        final.append(r.model_copy(update={"warnings": w, "confidence": _confidence(w)}))
    return final
```
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_validate.py` → `5 passed`
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/validate.py tests/test_validate.py && git commit -m "feat: 영수증 결정론 검증(금액·사업자번호·중복)"`

---

### Task 6: MCP stdio 클라이언트(1회성·세션 재사용) + Fake + fixture 녹화
**Files:** Create `src/receipt_evidence/mcp_client.py`, `tests/test_mcp_client.py`, `tests/fixtures/echo_mcp_server.py`, `scripts/record_fixtures.py`, `tests/integration/__init__.py`, `tests/integration/test_mcp_live.py`
**Interfaces:** Produces `McpResult(text: str, is_error: bool)`, `ToolCaller` Protocol (`call_many(calls: list[tuple[str, dict]]) -> list[McpResult]`, `__enter__() -> ToolCaller`, `__exit__(*exc) -> None`), `StdioToolCaller(command: str, args: list[str], env: dict[str, str] | None = None)`. `with` 안에서는 세션 1개를 재사용하고 밖에서는 호출마다 1회성 세션을 연다. 또 `law_caller() -> StdioToolCaller`, `kordoc_caller() -> StdioToolCaller`, `FakeToolCaller(handlers: dict[str, Callable[[dict], str]])`(`.calls`, 컨텍스트 매니저)를 제공한다.

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/fixtures/echo_mcp_server.py
"""오프라인 테스트용 MCP stdio 서버. 실행: python echo_mcp_server.py"""
import os
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("echo")

@mcp.tool()
def echo(text: str) -> str:
    return f"echo:{text}"

@mcp.tool()
def pid() -> str:
    return str(os.getpid())

@mcp.tool()
def boom() -> str:
    raise ValueError("boom")

if __name__ == "__main__":
    mcp.run()
```
```python
# tests/test_mcp_client.py
import sys
from pathlib import Path
from receipt_evidence.mcp_client import FakeToolCaller, McpResult, StdioToolCaller, kordoc_caller, law_caller

ECHO = [str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")]

def test_fake_caller_dispatches_and_records():
    f = FakeToolCaller({"search_law": lambda a: f"MST: 1 for {a['query']}"})
    res = f.call_many([("search_law", {"query": "공무원 여비 규정"})])
    assert res == [McpResult(text="MST: 1 for 공무원 여비 규정", is_error=False)] and f.calls[0][0] == "search_law"

def test_fake_caller_unknown_tool_is_error_and_context_manager():
    with FakeToolCaller({}) as f:
        assert f.call_many([("nope", {})])[0].is_error

def test_factory_commands():
    l, k = law_caller(), kordoc_caller()
    assert isinstance(l, StdioToolCaller) and l.command == "npx" and l.args == ["-y", "korean-law-mcp"] and l.env == {"LAW_OC": "kca-api"}
    assert k.args == ["-y", "kordoc", "mcp"] and k.env is None

def test_stdio_oneshot_spawns_per_call():
    c = StdioToolCaller(sys.executable, ECHO)
    p1 = c.call_many([("pid", {})])[0].text
    p2 = c.call_many([("pid", {})])[0].text
    assert p1.isdigit() and p1 != p2

def test_stdio_session_reuses_one_process_and_reports_tool_errors():
    with StdioToolCaller(sys.executable, ECHO) as c:
        a = c.call_many([("echo", {"text": "가"}), ("pid", {})])
        b = c.call_many([("pid", {}), ("boom", {})])
    assert a[0] == McpResult(text="echo:가", is_error=False) and a[1].text == b[0].text and b[1].is_error
```
```python
# tests/integration/test_mcp_live.py
import pytest
from receipt_evidence.mcp_client import law_caller, kordoc_caller

@pytest.mark.integration
def test_live_search_law():
    r = law_caller().call_many([("search_law", {"query": "공무원 여비 규정", "display": 5})])[0]
    assert not r.is_error and "MST: 287535" in r.text and "[현행]" in r.text

@pytest.mark.integration
def test_live_kordoc_generate_in_session(tmp_path):
    with kordoc_caller() as doc:
        for i in range(2):
            out = tmp_path / f"t{i}.hwpx"
            r = doc.call_many([("generate_document", {"markdown": "# 제목\n> 테스트하고자 함\n## 본문\n### 항목\n- 내용", "output_path": str(out), "preset": "보고서"})])[0]
            assert not r.is_error and out.exists()
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_mcp_client.py` → `ModuleNotFoundError: No module named 'receipt_evidence.mcp_client'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/mcp_client.py
from __future__ import annotations
import asyncio, threading
from dataclasses import dataclass, field
from typing import Callable, Protocol
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

@dataclass(frozen=True)
class McpResult:
    text: str
    is_error: bool

class ToolCaller(Protocol):
    def call_many(self, calls: list[tuple[str, dict]]) -> list[McpResult]: ...
    def __enter__(self) -> "ToolCaller": ...
    def __exit__(self, *exc) -> None: ...

async def _call_all(session: ClientSession, calls: list[tuple[str, dict]]) -> list[McpResult]:
    out: list[McpResult] = []
    for name, arguments in calls:
        res = await session.call_tool(name, arguments)
        text = "\n".join(c.text for c in (getattr(res, "content", None) or []) if isinstance(c, types.TextContent))
        # mcp 2.x는 is_error, 1.x는 isError — 둘 다 읽어 도구 오류를 성공으로 오인하지 않게 한다
        out.append(McpResult(text=text, is_error=bool(getattr(res, "is_error", None) or getattr(res, "isError", None))))
    return out

class StdioToolCaller:
    """`with` 밖: 호출마다 서버를 띄우는 1회성 세션. `with` 안: 백그라운드 스레드의 이벤트 루프에서 세션 1개를 재사용."""

    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None, timeout: float = 600.0):
        self.command = command
        self.args = list(args)
        self.env = env
        self.timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

    def _params(self) -> StdioServerParameters:
        return StdioServerParameters(command=self.command, args=self.args, env=self.env)

    def call_many(self, calls: list[tuple[str, dict]]) -> list[McpResult]:
        if self._loop is None:
            return asyncio.run(self._oneshot(calls))
        return asyncio.run_coroutine_threadsafe(self._submit(calls), self._loop).result(timeout=self.timeout)

    async def _oneshot(self, calls: list[tuple[str, dict]]) -> list[McpResult]:
        async with stdio_client(self._params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await _call_all(session, calls)

    async def _submit(self, calls: list[tuple[str, dict]]) -> list[McpResult]:
        fut = asyncio.get_running_loop().create_future()
        await self._queue.put((calls, fut))
        return await fut

    async def _serve(self, holder: dict, ready: threading.Event) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        try:
            async with stdio_client(self._params()) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    holder["loop"], holder["queue"] = asyncio.get_running_loop(), queue
                    ready.set()
                    while True:
                        item = await queue.get()
                        if item is None:
                            break
                        calls, fut = item
                        try:
                            fut.set_result(await _call_all(session, calls))
                        except Exception as e:
                            fut.set_exception(e)
        except BaseException as e:  # 세션이 비정상 종료되면 대기 중인 호출을 모두 실패로 끝낸다
            self._error = e
            while not queue.empty():
                item = queue.get_nowait()
                if item is not None and not item[1].done():
                    item[1].set_exception(RuntimeError(f"MCP 세션 종료: {e}"))
        finally:
            ready.set()

    def __enter__(self) -> "StdioToolCaller":
        holder: dict = {}
        ready = threading.Event()
        self._error = None
        self._thread = threading.Thread(target=lambda: asyncio.run(self._serve(holder, ready)), daemon=True)
        self._thread.start()
        ready.wait(timeout=180)
        if "loop" not in holder:
            self._thread.join(timeout=5)
            self._thread = None
            raise RuntimeError(f"MCP 서버 시작 실패({self.command} {' '.join(self.args)}): {self._error}")
        self._loop, self._queue = holder["loop"], holder["queue"]
        return self

    def __exit__(self, *exc) -> None:
        if self._loop is not None and self._thread is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop).result(timeout=10)
            except Exception:
                pass
            self._thread.join(timeout=30)
        self._loop = None
        self._queue = None
        self._thread = None

def law_caller() -> StdioToolCaller:
    return StdioToolCaller("npx", ["-y", "korean-law-mcp"], {"LAW_OC": "kca-api"})

def kordoc_caller() -> StdioToolCaller:
    return StdioToolCaller("npx", ["-y", "kordoc", "mcp"], None)

@dataclass
class FakeToolCaller:
    handlers: dict[str, Callable[[dict], str]]
    calls: list[tuple[str, dict]] = field(default_factory=list)

    def __enter__(self) -> "FakeToolCaller":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def call_many(self, calls: list[tuple[str, dict]]) -> list[McpResult]:
        out = []
        for name, args in calls:
            self.calls.append((name, args))
            h = self.handlers.get(name)
            out.append(McpResult(text=h(args), is_error=False) if h else McpResult(text=f"unknown tool {name}", is_error=True))
        return out
```
```python
# scripts/record_fixtures.py
"""실제 korean-law MCP 응답을 tests/fixtures/law/ 에 녹화한다. 실행: uv run python scripts/record_fixtures.py"""
from pathlib import Path
from receipt_evidence.mcp_client import law_caller

LAW = "공무원 여비 규정"
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "law"

def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with law_caller() as caller:
        search = caller.call_many([("search_law", {"query": LAW, "display": 5})])[0].text
        (OUT / "search_law.txt").write_text(search, encoding="utf-8")
        calls = [("get_annexes", {"lawName": LAW, "annexNo": "1", "knd": "1"}), ("get_annexes", {"lawName": LAW, "annexNo": "2", "knd": "1"})]
        calls += [("get_law_text", {"mst": "287535", "jo": jo}) for jo in ("제12조", "제13조", "제16조", "제18조")]
        names = ["annex1.html", "annex2.html", "jo12.txt", "jo13.txt", "jo16.txt", "jo18.txt"]
        for name, res in zip(names, caller.call_many(calls)):
            if res.is_error:
                raise SystemExit(f"{name}: {res.text}")
            (OUT / name).write_text(res.text, encoding="utf-8")
            print("saved", name, len(res.text))

if __name__ == "__main__":
    main()
```
`tests/integration/__init__.py`는 빈 파일.
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_mcp_client.py` → `5 passed`. 녹화: `uv run python scripts/record_fixtures.py` → `saved annex2.html …`, 7개 파일 생성(`search_law.txt` 포함). 라이브: `uv run pytest -m integration tests/integration/test_mcp_live.py` → `2 passed`
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/mcp_client.py tests/test_mcp_client.py tests/fixtures/echo_mcp_server.py scripts/record_fixtures.py tests/integration/__init__.py tests/integration/test_mcp_live.py tests/fixtures/law && git commit -m "feat: MCP stdio 클라이언트(세션 재사용), Fake, 법령 fixture 녹화"`

---

### Task 7: law (여비 규정 조회·별표 2 파서·스냅샷)
**Files:** Create `src/receipt_evidence/law.py`, `tests/test_law.py`, `tests/conftest.py`
**Interfaces:** Consumes `ToolCaller`, `LawSnapshot`, `RateTable`. Produces `LAW_NAME`, `ARTICLES`, `LawParseError`, `parse_search(text) -> tuple[str, str, date, date]`, `html_table_rows(html: str) -> list[list[str]]`, `parse_annex2(html: str) -> dict[str, RateTable]`, `fetch_law_snapshot(caller: ToolCaller) -> LawSnapshot`, `save_snapshot(s, path)`, `load_snapshot(path) -> LawSnapshot`, `get_law_snapshot(caller: ToolCaller, cache_dir: Path, today: date, *, refresh: bool = False) -> LawSnapshot` (`cache_dir/law/<YYYY-MM-DD>.json`)

- [ ] **Step 1: 실패하는 테스트 작성** (fixture는 Task 6 녹화본; `annex2.html`은 설계 문서 4절의 HTML과 동일)
```python
# tests/conftest.py
from datetime import date, datetime
from pathlib import Path
import pytest
from receipt_evidence.models import LawSnapshot, RateTable, TripConfig

FIX = Path(__file__).parent / "fixtures" / "law"

@pytest.fixture
def law_fixture_text():
    return {p.name: p.read_text(encoding="utf-8") for p in FIX.iterdir()}

@pytest.fixture
def law_snapshot():
    return LawSnapshot(law_name="공무원 여비 규정", law_id="009402", mst="287535", promulgated=date(2026, 6, 30), effective=date(2026, 7, 1),
        fetched_at=datetime(2026, 9, 13, 12, 0), rate_tables={
            "제1호": RateTable(grade="제1호", rail="실비(특실)", ship="실비(1등급)", air="실비", car="실비", daily_allowance=25000, lodging="실비", lodging_caps=None, meal_allowance=25000),
            "제2호": RateTable(grade="제2호", rail="실비(일반실)", ship="실비(2등급)", air="실비", car="실비", daily_allowance=25000, lodging="실비",
                                lodging_caps={"서울특별시": 100000, "광역시": 80000, "그 밖의 지역": 70000}, meal_allowance=25000)})

@pytest.fixture
def trip():
    return TripConfig(traveler_name="정백철", grade="제2호", org="한국방송통신전파진흥원", dept="", workplace_region="나주", destination_region="서울",
                      start_date=date(2026, 7, 9), end_date=date(2026, 7, 10), route_stations=["나주", "용산"], approval=["담당", "팀장"])
```
```python
# tests/test_law.py
from datetime import date
import pytest
from receipt_evidence.law import parse_search, parse_annex2, html_table_rows, fetch_law_snapshot, get_law_snapshot, LawParseError, save_snapshot, load_snapshot
from receipt_evidence.mcp_client import FakeToolCaller

def test_parse_search_current(law_fixture_text):
    assert parse_search(law_fixture_text["search_law.txt"]) == ("009402", "287535", date(2026, 6, 30), date(2026, 7, 1))

def test_parse_search_requires_current():
    with pytest.raises(LawParseError):
        parse_search("1. 공무원 여비 규정 [연혁]\n   - 법령ID: 1\n   - MST: 2\n   - 공포일: 20200101 / 시행일: 20200101")

def test_html_rows_join_br(law_fixture_text):
    rows = html_table_rows(law_fixture_text["annex2.html"])
    assert rows[3][0] == "구분" and "숙박비" in rows[3][6]

def test_parse_annex2(law_fixture_text):
    rt = parse_annex2(law_fixture_text["annex2.html"])
    assert rt["제2호"].lodging_caps == {"서울특별시": 100000, "광역시": 80000, "그 밖의 지역": 70000}
    assert rt["제1호"].lodging_caps is None and rt["제2호"].daily_allowance == 25000 and rt["제1호"].rail == "실비(특실)"

def test_parse_annex2_fails_loudly():
    with pytest.raises(LawParseError):
        parse_annex2("<table><tr><td>구분</td><td>철도운임</td></tr><tr><td>제2호</td><td>실비</td></tr></table>")

def test_fetch_snapshot_with_fake(law_fixture_text, tmp_path):
    t = law_fixture_text
    caller = FakeToolCaller({"search_law": lambda a: t["search_law.txt"],
                             "get_annexes": lambda a: t["annex1.html"] if a["annexNo"] == "1" else t["annex2.html"],
                             "get_law_text": lambda a: t["jo" + a["jo"][1:-1] + ".txt"]})
    s = fetch_law_snapshot(caller)
    assert s.mst == "287535" and "제16조" in s.articles and set(s.rate_tables) == {"제1호", "제2호"}
    assert all(c[1].get("mst", "287535") == "287535" for c in caller.calls)
    save_snapshot(s, tmp_path / "law.json"); assert load_snapshot(tmp_path / "law.json") == s

def test_daily_law_cache(law_fixture_text, tmp_path):
    t = law_fixture_text
    caller = FakeToolCaller({"search_law": lambda a: t["search_law.txt"],
                             "get_annexes": lambda a: t["annex1.html"] if a["annexNo"] == "1" else t["annex2.html"],
                             "get_law_text": lambda a: t["jo" + a["jo"][1:-1] + ".txt"]})
    s1 = get_law_snapshot(caller, tmp_path, date(2026, 9, 13))
    n = len(caller.calls)
    assert get_law_snapshot(caller, tmp_path, date(2026, 9, 13)) == s1 and len(caller.calls) == n
    assert (tmp_path / "law" / "2026-09-13.json").exists()
    get_law_snapshot(caller, tmp_path, date(2026, 9, 13), refresh=True)
    assert len(caller.calls) == 2 * n
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_law.py` → `ModuleNotFoundError: No module named 'receipt_evidence.law'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/law.py
from __future__ import annotations
import re
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from .mcp_client import ToolCaller
from .models import LawSnapshot, RateTable

LAW_NAME = "공무원 여비 규정"
ARTICLES = ["제12조", "제13조", "제16조", "제18조"]

class LawParseError(Exception):
    pass

def parse_search(text: str) -> tuple[str, str, date, date]:
    for block in re.split(r"\n(?=\s*\d+\.\s)", text):
        # "공무원 여비 규정 시행규칙" 같은 유사 법령 블록을 배제하려고 제목 줄을 정확히 매칭
        if re.search(rf"^\s*\d+\.\s+{re.escape(LAW_NAME)}\s+\[현행\]", block, flags=re.M):
            law_id = re.search(r"법령ID:\s*(\d+)", block)
            mst = re.search(r"MST:\s*(\d+)", block)
            dates = re.search(r"공포일:\s*(\d{8})\s*/\s*시행일:\s*(\d{8})", block)
            if law_id and mst and dates:
                p, e = dates.groups()
                return law_id.group(1), mst.group(1), date.fromisoformat(f"{p[:4]}-{p[4:6]}-{p[6:]}"), date.fromisoformat(f"{e[:4]}-{e[4:6]}-{e[6:]}")
    raise LawParseError("search_law 응답에서 [현행] 공무원 여비 규정의 MST/시행일을 찾지 못함")

class _Rows(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows: list[list[str]] = []; self._cell: list[str] | None = None
    def handle_starttag(self, tag, attrs):
        if tag == "tr": self.rows.append([])
        elif tag in ("td", "th"): self._cell = []
        elif tag == "br" and self._cell is not None: self._cell.append(" ")
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self.rows[-1].append(re.sub(r"\s+", " ", "".join(self._cell)).strip()); self._cell = None
    def handle_data(self, data):
        if self._cell is not None: self._cell.append(data)

def html_table_rows(html: str) -> list[list[str]]:
    p = _Rows(); p.feed(html); return p.rows

_CAP = re.compile(r"(서울특별시|광역시|그 밖의 지역)[은는]?\s*([\d,]+)")

def _won(s: str) -> int:
    m = re.search(r"[\d,]+", s)
    if not m:
        raise LawParseError(f"금액 파싱 실패: {s!r}")
    return int(m.group(0).replace(",", ""))

def parse_annex2(html: str) -> dict[str, RateTable]:
    rows = html_table_rows(html)
    header = next((r for r in rows if r and r[0] == "구분"), None)
    if not header or len(header) != 8:
        raise LawParseError("별표 2 헤더(구분…식비 8열)를 찾지 못함")
    idx = {name: i for i, name in enumerate(header)}
    col = lambda key: next(i for n, i in idx.items() if key in n)
    out: dict[str, RateTable] = {}
    for r in rows:
        if len(r) == 8 and r[0] in ("제1호", "제2호"):
            lodging = r[col("숙박비")]
            caps = {k: int(v.replace(",", "")) for k, v in _CAP.findall(lodging)} if "상한액" in lodging else None
            if caps is not None and len(caps) != 3:
                raise LawParseError(f"숙박비 상한액 파싱 불완전: {lodging!r}")
            out[r[0]] = RateTable(grade=r[0], rail=r[col("철도")].replace(" ", ""), ship=r[col("선박")].replace(" ", ""), air=r[col("항공")],
                                  car=r[col("자동차")], daily_allowance=_won(r[col("일비")]), lodging=lodging.split("(")[0].strip(),
                                  lodging_caps=caps, meal_allowance=_won(r[col("식비")]))
    if set(out) != {"제1호", "제2호"}:
        raise LawParseError("별표 2에서 제1호/제2호 행을 모두 찾지 못함")
    return out

def fetch_law_snapshot(caller: ToolCaller) -> LawSnapshot:
    search = caller.call_many([("search_law", {"query": LAW_NAME, "display": 5})])[0]
    if search.is_error:
        raise LawParseError(search.text)
    law_id, mst, prom, eff = parse_search(search.text)
    calls = [("get_annexes", {"lawName": LAW_NAME, "annexNo": "1", "knd": "1"}), ("get_annexes", {"lawName": LAW_NAME, "annexNo": "2", "knd": "1"})]
    calls += [("get_law_text", {"mst": mst, "jo": jo}) for jo in ARTICLES]
    res = caller.call_many(calls)
    for (name, args), r in zip(calls, res):
        if r.is_error:
            raise LawParseError(f"{name} {args}: {r.text}")
    return LawSnapshot(law_name=LAW_NAME, law_id=law_id, mst=mst, promulgated=prom, effective=eff, fetched_at=datetime.now(),
                       annexes={"별표1": res[0].text, "별표2": res[1].text}, articles={jo: r.text for jo, r in zip(ARTICLES, res[2:])},
                       rate_tables=parse_annex2(res[1].text))

def save_snapshot(s: LawSnapshot, path: Path) -> None:
    path.write_text(s.model_dump_json(indent=2), encoding="utf-8")

def load_snapshot(path: Path) -> LawSnapshot:
    return LawSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

def get_law_snapshot(caller: ToolCaller, cache_dir: Path, today: date, *, refresh: bool = False) -> LawSnapshot:
    """배치당 1회 조회. 같은 날짜 캐시가 있으면 재사용하고, refresh=True면 다시 조회한다."""
    path = cache_dir / "law" / f"{today.isoformat()}.json"
    if path.exists() and not refresh:
        return load_snapshot(path)
    snap = fetch_law_snapshot(caller)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_snapshot(snap, path)
    return snap
```
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_law.py` → `7 passed`
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/law.py tests/test_law.py tests/conftest.py && git commit -m "feat: korean-law 조회·별표2 파서·법령 스냅샷 일자 캐시"`

---

### Task 8: workspace (출장자/출장 폴더 발견 · 설정 · 자동 제안 · 사용자 확인값)
**Files:** Create `src/receipt_evidence/workspace.py`, `tests/test_workspace.py`, `examples/traveler.yaml`, `examples/trip.yaml`, `examples/overrides.yaml`
**Interfaces:** Consumes `SUPPORTED`(ingest), `Category, Receipt, TravelerProfile, TripConfig`. Produces:
- `TripJob(traveler: str, trip_id: str, traveler_dir: Path, trip_dir: Path)` (frozen dataclass)
- `discover(data_dir: Path, travelers: list[str] | None = None, trips: list[str] | None = None) -> tuple[list[TripJob], list[str]]` — 정렬된 작업 목록과 경고(루트·출장자 폴더에 바로 놓인 영수증, 영수증 없는 출장 폴더)
- `load_traveler(traveler_dir: Path) -> TravelerProfile` (traveler.yaml 없으면 이름=폴더명)
- `resolve_trip(job: TripJob, profile: TravelerProfile, receipts: list[Receipt]) -> TripConfig` (trip.yaml 우선, 없으면 `propose_trip`)
- `propose_trip(trip_id: str, receipts: list[Receipt], base: dict) -> TripConfig` (결제일만 있는 영수증은 기간 계산 제외)
- `dump_trip_yaml(trip: TripConfig) -> str`
- `load_overrides(trip_dir: Path) -> dict[str, dict]`, `apply_overrides(receipts: list[Receipt], overrides: dict[str, dict]) -> list[Receipt]` (`clear_warnings` 지원, `raw["overrides"]`에 기록)

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/test_workspace.py
from datetime import date, datetime
from pathlib import Path
import yaml
from receipt_evidence.models import Category, Receipt
from receipt_evidence.workspace import (TripJob, apply_overrides, discover, dump_trip_yaml, load_overrides, load_traveler,
                                        propose_trip, resolve_trip)

def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x")

def _tree(root: Path) -> Path:
    _touch(root / "loose.jpg")
    _touch(root / "정백철" / "stray.pdf")
    _touch(root / "정백철" / "2026-07-09_서울" / "a.jpg")
    _touch(root / "정백철" / "2026-08-03_부산" / "b.png")
    (root / "정백철" / "2026-09-01_빈폴더").mkdir(parents=True)
    _touch(root / "홍길동" / "2026-07-20_대전" / "c.pdf")
    return root

def test_discover_jobs_and_warnings(tmp_path):
    jobs, warnings = discover(_tree(tmp_path / "data"))
    assert [(j.traveler, j.trip_id) for j in jobs] == [("정백철", "2026-07-09_서울"), ("정백철", "2026-08-03_부산"), ("홍길동", "2026-07-20_대전")]
    assert any("loose.jpg" in w for w in warnings) and any("stray.pdf" in w for w in warnings) and any("2026-09-01_빈폴더" in w for w in warnings)

def test_discover_filters(tmp_path):
    jobs, _ = discover(_tree(tmp_path / "data"), travelers=["정백철"], trips=["2026-08-03_부산"])
    assert [(j.traveler, j.trip_id) for j in jobs] == [("정백철", "2026-08-03_부산")]

def test_load_traveler_default_and_yaml(tmp_path):
    d = tmp_path / "정백철"; d.mkdir()
    assert load_traveler(d).name == "정백철" and load_traveler(d).grade is None
    (d / "traveler.yaml").write_text("grade: 제2호\nworkplace_region: 나주\napproval: [담당, 팀장]\n", encoding="utf-8")
    p = load_traveler(d)
    assert p.grade == "제2호" and p.workplace_region == "나주" and p.name == "정백철" and p.approval == ["담당", "팀장"]

def _rail(rid, day, o, d):
    return Receipt(receipt_id=rid, image_id=rid, category=Category.RAIL, amount=48200, service_date=day, origin=o, destination=d)

def test_resolve_trip_prefers_yaml(tmp_path):
    trav = tmp_path / "정백철"; trip_dir = trav / "2026-07-09_서울"; trip_dir.mkdir(parents=True)
    (trav / "traveler.yaml").write_text("grade: 제2호\n", encoding="utf-8")
    (trip_dir / "trip.yaml").write_text("start_date: 2026-07-09\nend_date: 2026-07-10\ndestination_region: 서울\npurpose: 회의\n", encoding="utf-8")
    job = TripJob("정백철", "2026-07-09_서울", trav, trip_dir)
    t = resolve_trip(job, load_traveler(trav), [])
    assert t.proposed is False and t.days == 2 and t.purpose == "회의" and t.trip_id == "2026-07-09_서울"
    assert t.traveler_name == "정백철" and t.grade == "제2호"

def test_propose_trip_ignores_payment_only_dates():
    stay = Receipt(receipt_id="s", image_id="s", category=Category.LODGING, amount=100000, paid_at=datetime(2026, 8, 21, 16, 44))
    rs = [_rail("r1", date(2026, 7, 9), "나주", "용산"), _rail("r2", date(2026, 7, 10), "용산", "나주"), stay]
    t = propose_trip("2026-07-09_서울", rs, {"traveler_name": "정백철", "trip_id": "2026-07-09_서울"})
    assert t.proposed and (t.start_date, t.end_date) == (date(2026, 7, 9), date(2026, 7, 10))
    assert t.destination_region == "서울" and t.route_stations == ["나주", "용산"] and len(t.proposal_basis) == 3
    y = yaml.safe_load(dump_trip_yaml(t))
    assert y["start_date"] == "2026-07-09" and y["destination_region"] == "서울" and "traveler_name" not in y

def test_propose_trip_bad_folder_name():
    t = propose_trip("서울출장", [], {"traveler_name": "x", "trip_id": "서울출장"})
    assert t.proposed and t.start_date is None and t.days is None and t.destination_region == ""

def test_overrides_merge_and_clear_warnings(tmp_path):
    (tmp_path / "overrides.yaml").write_text("s:\n  service_date: 2026-07-09\n  region: 서울\n  clear_warnings: [MISSING_BIZNO]\n", encoding="utf-8")
    stay = Receipt(receipt_id="s", image_id="s", category=Category.LODGING, amount=100000, warnings=["MISSING_BIZNO", "MISSING_APPROVAL"])
    out = apply_overrides([stay, _rail("r1", date(2026, 7, 9), "나주", "용산")], load_overrides(tmp_path))
    assert out[0].service_date == date(2026, 7, 9) and out[0].region == "서울" and out[0].warnings == ["MISSING_APPROVAL"]
    assert out[0].raw["overrides"]["region"] == "서울" and out[1].receipt_id == "r1" and load_overrides(tmp_path / "none") == {}
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_workspace.py` → `ModuleNotFoundError: No module named 'receipt_evidence.workspace'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/workspace.py
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import yaml
from .ingest import SUPPORTED
from .models import Category, Receipt, TravelerProfile, TripConfig

TRIP_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_([^_]+)")
TRIP_YAML_FIELDS = ("start_date", "end_date", "destination_region", "purpose", "route_stations", "lodging_region",
                    "over_cap_reason", "taxi_reason", "official_vehicle", "within_workplace", "duration_hours")

@dataclass(frozen=True)
class TripJob:
    traveler: str
    trip_id: str
    traveler_dir: Path
    trip_dir: Path

def _is_receipt(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith(".")

def _subdirs(p: Path) -> list[Path]:
    return sorted(d for d in p.iterdir() if d.is_dir() and not d.name.startswith("."))

def discover(data_dir: Path, travelers: list[str] | None = None, trips: list[str] | None = None) -> tuple[list[TripJob], list[str]]:
    jobs: list[TripJob] = []
    warnings = [f"{p.name}: data/<출장자>/<출장>/ 폴더에 넣어야 처리돼요" for p in sorted(data_dir.iterdir()) if _is_receipt(p)]
    for tdir in _subdirs(data_dir):
        if travelers and tdir.name not in travelers:
            continue
        loose = sorted(p.name for p in tdir.iterdir() if _is_receipt(p))
        if loose:
            warnings.append(f"{tdir.name}/{', '.join(loose)}: 출장 폴더에 넣어야 처리돼요")
        for trip in _subdirs(tdir):
            if trips and trip.name not in trips:
                continue
            if not any(_is_receipt(p) for p in trip.iterdir()):
                warnings.append(f"{tdir.name}/{trip.name}: 영수증이 없어 건너뜀")
                continue
            jobs.append(TripJob(traveler=tdir.name, trip_id=trip.name, traveler_dir=tdir, trip_dir=trip))
    return jobs, warnings

def _yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

def load_traveler(traveler_dir: Path) -> TravelerProfile:
    data = _yaml(traveler_dir / "traveler.yaml")
    data.setdefault("name", traveler_dir.name)
    return TravelerProfile.model_validate(data)

def _base(job: TripJob, profile: TravelerProfile) -> dict:
    return {"traveler_name": profile.name, "trip_id": job.trip_id, "position": profile.position, "grade": profile.grade,
            "org": profile.org, "dept": profile.dept, "workplace_region": profile.workplace_region, "approval": profile.approval}

def resolve_trip(job: TripJob, profile: TravelerProfile, receipts: list[Receipt]) -> TripConfig:
    trip_yaml = job.trip_dir / "trip.yaml"
    if trip_yaml.exists():
        return TripConfig.model_validate(_base(job, profile) | _yaml(trip_yaml))
    return propose_trip(job.trip_id, receipts, _base(job, profile))

def propose_trip(trip_id: str, receipts: list[Receipt], base: dict) -> TripConfig:
    basis: list[str] = []
    folder_date, destination = None, ""
    m = TRIP_DIR_RE.match(trip_id)
    if m:
        destination = m.group(2)
        try:
            folder_date = date.fromisoformat(m.group(1))
        except ValueError:
            folder_date = None
    service = sorted(r.service_date for r in receipts if r.service_date)  # 결제일(paid_at)만 있는 영수증은 기간에 넣지 않는다
    ends = sorted(r.service_end_date for r in receipts if r.service_end_date)
    starts = [d for d in (folder_date, service[0] if service else None) if d]
    finishes = [d for d in (folder_date, service[-1] if service else None, ends[-1] if ends else None) if d]
    if folder_date:
        basis.append(f"폴더명 날짜 {folder_date}")
    if service:
        basis.append(f"영수증 운행·체크인일 {service[0]}~{service[-1]}")
    stations: list[str] = []
    for r in receipts:
        if r.category is Category.RAIL:
            for s in (r.origin, r.destination):
                if s and s not in stations:
                    stations.append(s)
    if stations:
        basis.append(f"철도 구간 {'·'.join(stations)}")
    return TripConfig.model_validate(base | {
        "destination_region": destination, "start_date": min(starts) if starts else None, "end_date": max(finishes) if finishes else None,
        "route_stations": stations, "proposed": True, "proposal_basis": basis})

def dump_trip_yaml(trip: TripConfig) -> str:
    data = trip.model_dump(mode="json", include=set(TRIP_YAML_FIELDS))
    header = "# 자동 제안된 출장 정보 — 확인·수정 후 data/<출장자>/<출장>/trip.yaml 로 저장하세요\n"
    header += "".join(f"# 근거: {b}\n" for b in trip.proposal_basis)
    return header + yaml.safe_dump({k: data[k] for k in TRIP_YAML_FIELDS}, allow_unicode=True, sort_keys=False)

def load_overrides(trip_dir: Path) -> dict[str, dict]:
    return {str(k): (v or {}) for k, v in _yaml(trip_dir / "overrides.yaml").items()}

def apply_overrides(receipts: list[Receipt], overrides: dict[str, dict]) -> list[Receipt]:
    out: list[Receipt] = []
    for r in receipts:
        o = overrides.get(r.receipt_id)
        if not o:
            out.append(r)
            continue
        cleared = set(o.get("clear_warnings", []))
        fields = {k: v for k, v in o.items() if k not in ("warnings", "clear_warnings", "receipt_id", "image_id", "sha256")}
        merged = r.model_dump() | fields
        merged["warnings"] = [w for w in r.warnings if w not in cleared]
        merged["raw"] = r.raw | {"overrides": o}
        out.append(Receipt.model_validate(merged))
    return out
```
```yaml
# examples/traveler.yaml — data/<출장자>/traveler.yaml 로 복사해 수정
position: ""
grade: 제2호              # 별표1 여비 지급 구분(제1호/제2호). 비우면 철도·숙박이 확인필요
org: ""
dept: ""
workplace_region: 나주
approval: [담당, 팀장, 부장]
```
```yaml
# examples/trip.yaml — data/<출장자>/<출장>/trip.yaml 로 복사해 수정 (없으면 영수증으로 자동 제안)
start_date: 2026-07-09
end_date: 2026-07-10
destination_region: 서울
purpose: ""
route_stations: [나주, 용산]
lodging_region: null      # 숙박 영수증에 지역이 없으면 "서울" 등
over_cap_reason: null     # 숙박비 상한 초과 시 부득이한 사유
taxi_reason: null         # 택시 이용 사유
official_vehicle: false
within_workplace: false
duration_hours: null
```
```yaml
# examples/overrides.yaml — data/<출장자>/<출장>/overrides.yaml (영수증에 없는 사실·오인식을 사용자 확인값으로 교정)
# 키는 out/<출장자>/<출장>/work/receipts.json 의 receipt_id
0123abcd4567-p1:
  service_date: 2026-07-09
  region: 서울
  clear_warnings: [MISSING_BIZNO]
```
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_workspace.py` → `7 passed`
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/workspace.py tests/test_workspace.py examples && git commit -m "feat: 출장자/출장 폴더 발견, 설정·자동 제안·사용자 확인값"`

---

### Task 9: rules (규칙 레지스트리 · 교차검사 · 정액 행)
**Files:** Create `src/receipt_evidence/rules.py`, `tests/test_rules.py`, `tests/fixtures/golden/receipts.json`
**Interfaces:** Consumes `Receipt, TripConfig, LawSnapshot, RateTable, Decision, Verdict, ERROR_CODES`. Produces:
- `RULES_VERSION: str`, `CROSS_CODES: frozenset[str]`, `WARNING_TEXT: dict[str, str]`, `ITEM: dict[Category, str]`
- `RULES: dict[Category, Handler]` where `Handler = Callable[[Receipt, TripConfig, RateTable, str, int], Decision]`
- `region_key(region: str) -> str`, `decide_receipt(r, trip, law) -> Decision`, `allowance_rows(trip, law) -> list[Decision]`, `decide_all(receipts, trip, law) -> list[Decision]`
- `apply_cross_checks(receipts: list[Receipt], trip: TripConfig) -> list[Receipt]` (LODGING_OVERLAP, LODGING_NIGHTS_EXCEED, DUP_TICKET)
- `mark_cross_trip_duplicates(receipts_by_trip: dict[str, list[Receipt]]) -> dict[str, list[Receipt]]` (DUP_ACROSS_TRIPS)
- `totals(decisions) -> dict[str, int]` (`claimed`, `approved`, `review`), `review_items(decisions, receipts) -> list[str]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/fixtures/golden/receipts.json` (data/ 3건 골든값, 카드번호·이메일 제외 — JSON이므로 주석 줄을 넣지 말 것):
```json
[
 {"receipt_id": "ktx1", "image_id": "ktx1", "category": "철도", "merchant": "한국철도공사", "business_no": "314-82-10024", "amount": 48200,
  "paid_at": "2026-07-08T22:10:00", "service_date": "2026-07-09", "origin": "나주", "destination": "용산", "seat_class": "일반실", "train_no": "KTX-산천 424", "approval_no": "55431218"},
 {"receipt_id": "ktx2", "image_id": "ktx2", "category": "철도", "merchant": "한국철도공사", "business_no": "314-82-10024", "amount": 48200,
  "paid_at": "2026-06-11T08:31:00", "service_date": "2026-07-10", "origin": "용산", "destination": "나주", "seat_class": "일반실", "train_no": "KTX 433", "approval_no": "49243338"},
 {"receipt_id": "stay", "image_id": "stay", "category": "숙박", "merchant": "(주)여기어때컴퍼니", "amount": 100000,
  "paid_at": "2026-08-21T16:44:06", "approval_no": "68325420", "payer_name": "정백철", "warnings": ["MISSING_BIZNO"]}
]
```
```python
# tests/test_rules.py
import json
from datetime import date
from pathlib import Path
from receipt_evidence.models import Receipt, Category, Verdict
from receipt_evidence.rules import (RULES, allowance_rows, apply_cross_checks, decide_all, decide_receipt, mark_cross_trip_duplicates,
                                    region_key, review_items, totals)

GOLD = [Receipt(**d) for d in json.loads((Path(__file__).parent / "fixtures/golden/receipts.json").read_text(encoding="utf-8"))]

def test_region_key():
    assert region_key("서울 강남구") == "서울특별시" and region_key("부산광역시") == "광역시" and region_key("광주") == "광역시"
    assert region_key("전남 나주시") == "그 밖의 지역" and region_key("경기도 수원") == "그 밖의 지역"

def test_golden_three(trip, law_snapshot):
    ds = decide_all(GOLD, trip, law_snapshot)
    by = {d.receipt_id: d for d in ds if d.receipt_id}
    assert by["ktx1"].verdict is Verdict.PAY and by["ktx1"].approved_amount == 48200 and "별표2 제2호 철도운임 실비(일반실)" in by["ktx1"].basis
    assert by["ktx2"].verdict is Verdict.PAY
    assert by["stay"].verdict is Verdict.REVIEW and by["stay"].approved_amount == 0 and any("출장기간" in r for r in by["stay"].reasons)
    items = {d.item: d for d in ds if d.receipt_id is None}
    assert items["일비"].approved_amount == 50000 and items["식비"].approved_amount == 50000
    # 청구 = 영수증 48,200+48,200+100,000 / 인정 = 철도 96,400 + 일비 50,000 + 식비 50,000 / 확인필요 = 숙박 100,000
    assert totals(ds) == {"claimed": 196400, "approved": 196400, "review": 100000}
    assert any("stay" in x for x in review_items(ds, GOLD))

def test_lodging_cap_and_over_cap(trip, law_snapshot):
    stay = GOLD[2].model_copy(update={"service_date": date(2026, 7, 9), "region": "서울", "amount": 150000, "warnings": []})
    d = decide_receipt(stay, trip, law_snapshot)
    assert d.verdict is Verdict.REDUCED and d.approved_amount == 100000
    d2 = decide_receipt(stay, trip.model_copy(update={"over_cap_reason": "행사장 인근 만실"}), law_snapshot)
    assert d2.verdict is Verdict.REDUCED and d2.approved_amount == 130000 and "제16조제1항 단서" in d2.basis
    d3 = decide_receipt(stay.model_copy(update={"amount": 120000}), trip.model_copy(update={"over_cap_reason": "만실"}), law_snapshot)
    assert d3.verdict is Verdict.PAY and d3.approved_amount == 120000

def test_lodging_region_unknown_and_grade1(trip, law_snapshot):
    stay = GOLD[2].model_copy(update={"service_date": date(2026, 7, 9), "warnings": []})
    assert decide_receipt(stay, trip, law_snapshot).verdict is Verdict.REVIEW
    assert decide_receipt(stay, trip.model_copy(update={"lodging_region": "서울"}), law_snapshot).verdict is Verdict.PAY
    assert decide_receipt(stay, trip.model_copy(update={"grade": "제1호"}), law_snapshot).approved_amount == 100000

def test_rail_special_class_and_route(trip, law_snapshot):
    sp = GOLD[0].model_copy(update={"seat_class": "특실"})
    assert decide_receipt(sp, trip, law_snapshot).verdict is Verdict.REVIEW
    off = GOLD[0].model_copy(update={"origin": "부산", "destination": "대구"})
    assert decide_receipt(off, trip, law_snapshot).verdict is Verdict.REVIEW
    assert decide_receipt(GOLD[0], trip.model_copy(update={"grade": None}), law_snapshot).verdict is Verdict.REVIEW

def test_taxi_meal_and_errors(trip, law_snapshot):
    taxi = Receipt(receipt_id="t", image_id="t", category=Category.TAXI, amount=12000, service_date=date(2026, 7, 9))
    assert decide_receipt(taxi, trip, law_snapshot).verdict is Verdict.REVIEW
    assert decide_receipt(taxi, trip.model_copy(update={"taxi_reason": "심야 대중교통 종료"}), law_snapshot).verdict is Verdict.PAY
    meal = Receipt(receipt_id="m", image_id="m", category=Category.MEAL, amount=9000, service_date=date(2026, 7, 9))
    assert decide_receipt(meal, trip, law_snapshot).verdict is Verdict.DENIED
    bad = GOLD[0].model_copy(update={"warnings": ["DUP_APPROVAL"]})
    d = decide_receipt(bad, trip, law_snapshot)
    assert d.verdict is Verdict.REVIEW and "승인번호" in d.reasons[0]

def test_within_workplace_and_official_vehicle(trip, law_snapshot):
    t = trip.model_copy(update={"within_workplace": True, "duration_hours": 5})
    rows = allowance_rows(t, law_snapshot)
    assert [r.item for r in rows] == ["근무지 내 출장 여비"] and rows[0].approved_amount == 20000
    assert decide_receipt(GOLD[0], t, law_snapshot).verdict is Verdict.DENIED
    half = allowance_rows(trip.model_copy(update={"official_vehicle": True}), law_snapshot)
    assert {r.item: r.approved_amount for r in half} == {"일비": 25000, "식비": 50000}
    assert allowance_rows(trip.model_copy(update={"start_date": None}), law_snapshot)[0].verdict is Verdict.REVIEW

def test_registry_covers_receipt_categories():
    assert {Category.RAIL, Category.BUS, Category.AIR, Category.TAXI, Category.LODGING, Category.MEAL} <= set(RULES)

def test_cross_checks_lodging_overlap_nights_and_dup_ticket(trip, law_snapshot):
    s1 = Receipt(receipt_id="s1", image_id="s1", category=Category.LODGING, amount=90000, service_date=date(2026, 7, 9), nights=1, region="서울")
    s2 = s1.model_copy(update={"receipt_id": "s2", "image_id": "s2"})
    k2 = GOLD[0].model_copy(update={"receipt_id": "k2", "image_id": "k2", "approval_no": "1"})
    out = {r.receipt_id: r for r in apply_cross_checks([s1, s2, GOLD[0], k2, GOLD[1]], trip)}
    assert "LODGING_OVERLAP" in out["s1"].warnings and "LODGING_NIGHTS_EXCEED" in out["s2"].warnings
    assert "DUP_TICKET" in out["ktx1"].warnings and "DUP_TICKET" in out["k2"].warnings and out["ktx2"].warnings == []
    d = decide_receipt(out["s1"], trip, law_snapshot)
    assert d.verdict is Verdict.REVIEW and "겹침" in d.reasons[0]

def test_cross_trip_duplicates():
    a = GOLD[0].model_copy(update={"sha256": "same"})
    b = GOLD[1].model_copy(update={"sha256": "other"})
    c = GOLD[0].model_copy(update={"sha256": "same", "receipt_id": "x"})
    out = mark_cross_trip_duplicates({"t1": [a, b], "t2": [c]})
    assert "DUP_ACROSS_TRIPS" in out["t1"][0].warnings and out["t1"][1].warnings == [] and "DUP_ACROSS_TRIPS" in out["t2"][0].warnings

def test_proposed_trip_allowances_need_review(trip, law_snapshot):
    rows = allowance_rows(trip.model_copy(update={"proposed": True}), law_snapshot)
    assert [r.item for r in rows] == ["일비", "식비"]
    assert all(r.verdict is Verdict.REVIEW and r.approved_amount == 0 for r in rows) and "50,000" in rows[0].reasons[0]
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_rules.py` → `ModuleNotFoundError: No module named 'receipt_evidence.rules'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/rules.py
from __future__ import annotations
from collections.abc import Callable
from datetime import date, timedelta
from .models import Category, Decision, LawSnapshot, RateTable, Receipt, TripConfig, Verdict
from .validate import ERROR_CODES

RULES_VERSION = "r1"  # 판정 로직을 바꾸면 올린다 → fingerprint가 달라져 새 버전 문서가 생성됨
CROSS_CODES = frozenset({"LODGING_OVERLAP", "LODGING_NIGHTS_EXCEED", "DUP_TICKET", "DUP_ACROSS_TRIPS"})
WARNING_TEXT = {
    "MISSING_AMOUNT": "금액을 읽지 못함", "AMOUNT_NOT_IN_TRANSCRIPT": "금액이 영수증 원문에서 확인되지 않음",
    "BIZNO_CHECKSUM": "사업자등록번호 검증 실패", "DUP_APPROVAL": "승인번호가 다른 영수증과 같음",
    "EXTRACT_FAILED": "영수증을 읽지 못함", "MISSING_DATE": "날짜를 읽지 못함",
    "LODGING_OVERLAP": "다른 숙박 영수증과 날짜가 겹침", "LODGING_NIGHTS_EXCEED": "숙박 박수 합계가 출장 박수보다 많음",
    "DUP_TICKET": "같은 날짜·편명·구간의 승차권이 중복", "DUP_ACROSS_TRIPS": "같은 영수증 파일이 다른 출장에도 있음",
}
_METRO = ("부산", "대구", "인천", "대전", "울산", "광주")
ITEM = {Category.RAIL: "철도운임", Category.BUS: "버스운임", Category.AIR: "항공운임", Category.TAXI: "자동차운임(택시)",
        Category.LODGING: "숙박비", Category.MEAL: "식비(영수증)", Category.OTHER: "기타", Category.UNKNOWN: "미상"}
Handler = Callable[[Receipt, TripConfig, RateTable, str, int], Decision]

def region_key(region: str) -> str:
    r = region.replace(" ", "")
    if "서울" in r:
        return "서울특별시"
    if "광역시" in r or any(r.startswith(m) for m in _METRO):
        return "광역시"
    return "그 밖의 지역"

def _d(r: Receipt, item: str, claimed: int, approved: int, v: Verdict, basis: list[str], reasons: list[str]) -> Decision:
    return Decision(receipt_id=r.receipt_id, item=item, claimed_amount=claimed, approved_amount=approved, verdict=v, basis=basis, reasons=reasons)

def _base_date(r: Receipt) -> date | None:
    return r.service_date or (r.paid_at.date() if r.paid_at else None)

def _rail(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    if trip.route_stations and not any(s in (r.origin or "") or s in (r.destination or "") for s in trip.route_stations):
        return _d(r, item, amt, 0, Verdict.REVIEW, [], [f"구간 {r.origin}→{r.destination}이 출장 경로 {trip.route_stations}와 불일치"])
    if trip.grade == "제2호" and "특실" in (r.seat_class or ""):
        return _d(r, item, amt, 0, Verdict.REVIEW, ["별표2 제2호 철도운임 실비(일반실)"], ["특실 이용: 일반실 운임 차액 확인 필요"])
    return _d(r, item, amt, amt, Verdict.PAY, [f"별표2 {trip.grade} 철도운임 {rt.rail}", "별표2 비고 6"], [f"{r.train_no or '철도'} {r.origin}→{r.destination} 실비"])

def _bus(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    return _d(r, item, amt, amt, Verdict.PAY, ["별표2 비고 3"], ["버스요금 실비"])

def _air(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    return _d(r, item, amt, amt, Verdict.PAY, ["제12조", "별표2 항공운임 실비"], ["항공운임 실비"])

def _taxi(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    if trip.taxi_reason:
        return _d(r, item, amt, amt, Verdict.PAY, ["제13조", "별표2 자동차운임 실비"], [f"부득이한 사유: {trip.taxi_reason}"])
    return _d(r, item, amt, 0, Verdict.REVIEW, ["제13조"], ["택시 이용의 부득이한 사유(taxi_reason) 미기재"])

def _lodging(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    nights = r.nights or 1
    if trip.grade == "제1호":
        return _d(r, item, amt, amt, Verdict.PAY, ["별표2 제1호 숙박비 실비", "제16조제4항"], [f"{nights}박 실비"])
    region = r.region or trip.lodging_region
    if not region:
        return _d(r, item, amt, 0, Verdict.REVIEW, ["별표2 제2호 숙박비 상한"], ["숙박 지역 미확인(영수증에 숙소명·주소 없음). trip.yaml lodging_region 또는 overrides.yaml region 입력 필요"])
    key = region_key(region)
    cap = rt.lodging_caps[key] * nights
    basis = [f"별표2 제2호 숙박비 상한({key} {rt.lodging_caps[key]:,})", "제16조제4항"]
    if amt <= cap:
        return _d(r, item, amt, amt, Verdict.PAY, basis, [f"{nights}박, 상한 {cap:,} 이내"])
    if trip.over_cap_reason:
        limit = int(cap * 1.3)
        basis.append("제16조제1항 단서")
        if amt <= limit:
            return _d(r, item, amt, amt, Verdict.PAY, basis, [f"상한 초과분 30% 이내 추가지급, 사유: {trip.over_cap_reason}"])
        return _d(r, item, amt, limit, Verdict.REDUCED, basis, [f"상한의 130%({limit:,})로 감액, 사유: {trip.over_cap_reason}"])
    return _d(r, item, amt, cap, Verdict.REDUCED, basis, [f"상한 {cap:,} 초과분 불인정(부득이한 사유 없음)"])

def _meal(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    return _d(r, item, amt, 0, Verdict.DENIED, ["제16조제5항", "별표2 식비"], ["식비는 여행일수 정액 지급, 영수증 실비 불인정"])

def _unknown(r: Receipt, trip: TripConfig, rt: RateTable, item: str, amt: int) -> Decision:
    return _d(r, item, amt, 0, Verdict.REVIEW, [], ["영수증 종류를 판별하지 못함"])

RULES: dict[Category, Handler] = {Category.RAIL: _rail, Category.BUS: _bus, Category.AIR: _air, Category.TAXI: _taxi,
                                  Category.LODGING: _lodging, Category.MEAL: _meal}

def decide_receipt(r: Receipt, trip: TripConfig, law: LawSnapshot) -> Decision:
    item, amt = ITEM[r.category], r.amount or 0
    blocking = [w for w in r.warnings if w in ERROR_CODES or w in CROSS_CODES]
    if r.amount is None or blocking:
        codes = blocking or ["MISSING_AMOUNT"]
        return _d(r, item, amt, 0, Verdict.REVIEW, [], ["검증 경고: " + ", ".join(WARNING_TEXT.get(c, c) for c in codes)])
    if trip.within_workplace and r.category in (Category.RAIL, Category.BUS, Category.AIR, Category.TAXI, Category.LODGING):
        return _d(r, item, amt, 0, Verdict.DENIED, ["제18조"], ["근무지 내 출장은 정액 지급 대상(운임·숙박비 별도 불인정)"])
    bd = _base_date(r)
    if trip.start_date and trip.end_date and bd and not (trip.start_date <= bd <= trip.end_date):
        return _d(r, item, amt, 0, Verdict.REVIEW, [], [f"기준일 {bd}이 출장기간 {trip.start_date}~{trip.end_date} 밖"])
    if trip.grade is None and r.category in (Category.RAIL, Category.LODGING):
        return _d(r, item, amt, 0, Verdict.REVIEW, ["별표1"], ["출장자 여비 지급 구분(제1호/제2호) 미확정 — traveler.yaml grade 입력 필요"])
    rt = law.rate_tables[trip.grade or "제2호"]
    return RULES.get(r.category, _unknown)(r, trip, rt, item, amt)

def allowance_rows(trip: TripConfig, law: LawSnapshot) -> list[Decision]:
    rt = law.rate_tables[trip.grade or "제2호"]
    mk = lambda item, appr, v, basis, reasons: Decision(receipt_id=None, item=item, claimed_amount=0, approved_amount=appr, verdict=v, basis=basis, reasons=reasons)
    if trip.within_workplace:
        if trip.duration_hours is None:
            return [mk("근무지 내 출장 여비", 0, Verdict.REVIEW, ["제18조"], ["출장 시간(duration_hours) 미입력"])]
        amt = (20000 if trip.duration_hours >= 4 else 10000) - (10000 if trip.official_vehicle else 0)
        return [mk("근무지 내 출장 여비", max(amt, 0), Verdict.PAY, ["제18조"], [f"{trip.duration_hours}시간, 공용차량 {'이용' if trip.official_vehicle else '미이용'}"])]
    days = trip.days
    if days is None:
        return [mk("일비", 0, Verdict.REVIEW, ["별표2", "제16조제3항"], ["출장기간 미입력"]), mk("식비", 0, Verdict.REVIEW, ["별표2", "제16조제5항"], ["출장기간 미입력"])]
    daily = rt.daily_allowance * days // (2 if trip.official_vehicle else 1)
    meal = rt.meal_allowance * days
    daily_note = f"{rt.daily_allowance:,}×{days}일" + (" ×1/2(공용차량)" if trip.official_vehicle else "")
    meal_note = f"{rt.meal_allowance:,}×{days}일"
    if trip.proposed:
        note = "출장기간이 자동 제안값 — trip.yaml로 확정 필요"
        return [mk("일비", 0, Verdict.REVIEW, ["별표2", "제16조제3항"], [f"{daily_note} = {daily:,} 예정", note]),
                mk("식비", 0, Verdict.REVIEW, ["별표2", "제16조제5항"], [f"{meal_note} = {meal:,} 예정", note])]
    return [mk("일비", daily, Verdict.PAY, ["별표2", "제16조제3항"], [daily_note]),
            mk("식비", meal, Verdict.PAY, ["별표2", "제16조제5항"], [meal_note])]

def decide_all(receipts: list[Receipt], trip: TripConfig, law: LawSnapshot) -> list[Decision]:
    return [decide_receipt(r, trip, law) for r in receipts] + allowance_rows(trip, law)

def _add_warnings(r: Receipt, codes: set[str]) -> Receipt:
    new = [c for c in sorted(codes) if c not in r.warnings]
    return r.model_copy(update={"warnings": r.warnings + new}) if new else r

def apply_cross_checks(receipts: list[Receipt], trip: TripConfig) -> list[Receipt]:
    add: dict[str, set[str]] = {r.receipt_id: set() for r in receipts}
    lodging = [r for r in receipts if r.category is Category.LODGING]
    dated = [r for r in lodging if r.service_date]
    span = lambda r: (r.service_date, r.service_end_date or (r.service_date + timedelta(days=r.nights or 1)))
    for i, a in enumerate(dated):
        a0, a1 = span(a)
        for b in dated[i + 1:]:
            b0, b1 = span(b)
            if a0 < b1 and b0 < a1:
                add[a.receipt_id].add("LODGING_OVERLAP")
                add[b.receipt_id].add("LODGING_OVERLAP")
    if trip.days is not None and lodging and sum(r.nights or 1 for r in lodging) > max(trip.days - 1, 0):
        for r in lodging:
            add[r.receipt_id].add("LODGING_NIGHTS_EXCEED")
    tickets: dict[tuple, list[str]] = {}
    for r in receipts:
        if r.category in (Category.RAIL, Category.BUS, Category.AIR) and r.service_date and r.train_no:
            tickets.setdefault((r.category, r.train_no, r.service_date, r.origin, r.destination), []).append(r.receipt_id)
    for ids in tickets.values():
        if len(ids) > 1:
            for rid in ids:
                add[rid].add("DUP_TICKET")
    return [_add_warnings(r, add[r.receipt_id]) for r in receipts]

def mark_cross_trip_duplicates(receipts_by_trip: dict[str, list[Receipt]]) -> dict[str, list[Receipt]]:
    owners: dict[str, set[str]] = {}
    for key, rs in receipts_by_trip.items():
        for r in rs:
            if r.sha256:
                owners.setdefault(r.sha256, set()).add(key)
    dup = {sha for sha, keys in owners.items() if len(keys) > 1}
    return {key: [_add_warnings(r, {"DUP_ACROSS_TRIPS"}) if r.sha256 in dup else r for r in rs] for key, rs in receipts_by_trip.items()}

def totals(decisions: list[Decision]) -> dict[str, int]:
    return {"claimed": sum(d.claimed_amount for d in decisions), "approved": sum(d.approved_amount for d in decisions),
            "review": sum(d.claimed_amount for d in decisions if d.verdict is Verdict.REVIEW)}

def review_items(decisions: list[Decision], receipts: list[Receipt]) -> list[str]:
    return [f"[{d.receipt_id or d.item}] {d.item}: " + "; ".join(d.reasons) for d in decisions if d.verdict is Verdict.REVIEW]
```
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_rules.py` → `11 passed`
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/rules.py tests/test_rules.py tests/fixtures/golden/receipts.json && git commit -m "feat: 규칙 레지스트리·교차검사와 data/ 골든 판정"`

---

### Task 10: report (마크다운 빌더 · 정렬 · 자동 제안 표기)
**Files:** Create `src/receipt_evidence/report.py`, `tests/test_report.py`
**Interfaces:** Consumes `Receipt, Decision, TripConfig, LawSnapshot, Category, totals`. Produces `DETAIL_HEADERS: list[str]`, `fmt_won(n: int) -> str`, `md_table(headers: list[str], rows: list[list[str]]) -> str`, `order_decisions(decisions, receipts) -> list[Decision]`(영수증: 기준일→구분, 정액 행은 뒤에 원래 순서), `build_markdown(trip, law, receipts, decisions, image_names: dict[str, list[str]], version: int | None = None) -> str`

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/test_report.py
import json
from datetime import date
from pathlib import Path
from receipt_evidence.models import Receipt
from receipt_evidence.rules import decide_all
from receipt_evidence.report import build_markdown, md_table, fmt_won, DETAIL_HEADERS

GOLD = [Receipt(**d) for d in json.loads((Path(__file__).parent / "fixtures/golden/receipts.json").read_text(encoding="utf-8"))]

def test_md_table_escapes_pipe():
    assert md_table(["a", "b"], [["x|y", "1"]]) == "| a | b |\n| --- | --- |\n| x\\|y | 1 |"

def test_build_markdown_structure(trip, law_snapshot):
    ds = decide_all(GOLD, trip, law_snapshot)
    md = build_markdown(trip, law_snapshot, GOLD, ds, {"ktx1": ["ktx1.jpg"], "stay": ["stay-p1.jpg", "stay-p2.jpg"]})
    lines = md.splitlines()
    assert lines[0] == "# 출장여비 영수증 증빙내역서" and lines[1].startswith("> ") and lines[1].endswith("하고자 함")
    for h in ("## 출장 개요", "## 지급대상 요약", "## 영수증별 상세", "## 적용 규정", "## 확인필요 사항", "## 붙임"):
        assert h in md
    assert "| " + " | ".join(DETAIL_HEADERS) + " |" in md and "| 합계 |" in md and fmt_won(196400) in md
    assert "![](ktx1.jpg)" in md and "![](stay-p2.jpg)" in md and "287535" not in md and "2026. 7. 1. 시행" in md
    assert "53618190" not in md and "| 영수증 | 3건 |" in md and "자동 제안" not in md

def test_sorting_version_and_proposed_notice(trip, law_snapshot):
    late = GOLD[1].model_copy(update={"receipt_id": "late", "image_id": "late"})
    early = GOLD[0].model_copy(update={"receipt_id": "early", "image_id": "early"})
    t = trip.model_copy(update={"proposed": True, "proposal_basis": ["폴더명 날짜 2026-07-09"]})
    ds = decide_all([late, early], t, law_snapshot)
    md = build_markdown(t, law_snapshot, [late, early], ds, {}, version=2)
    detail = md[md.index("## 영수증별 상세"):]
    assert detail.index("2026. 7. 9.") < detail.index("2026. 7. 10.") < detail.index("정액")
    assert "※ 출장 정보는 영수증으로 자동 제안한 값입니다(근거: 폴더명 날짜 2026-07-09)" in md and "| 문서 버전 | v2 |" in md
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_report.py` → `ModuleNotFoundError: No module named 'receipt_evidence.report'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/report.py
from __future__ import annotations
from collections import defaultdict
from datetime import date
from .models import Category, Decision, LawSnapshot, Receipt, TripConfig, Verdict
from .rules import totals

DETAIL_HEADERS = ["연번", "일자", "구분", "가맹점", "승인번호", "결제액", "인정액", "판정", "근거"]
_CAT_ORDER = {c: i for i, c in enumerate(Category)}

def fmt_won(n: int) -> str:
    return f"{n:,}"

def _cell(s: object) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")

def md_table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)

def _kdate(d: date | None) -> str:
    return f"{d.year}. {d.month}. {d.day}." if d else "미정"

def _receipt_day(r: Receipt) -> date | None:
    return r.service_date or (r.paid_at.date() if r.paid_at else None)

def order_decisions(decisions: list[Decision], receipts: list[Receipt]) -> list[Decision]:
    by_id = {r.receipt_id: r for r in receipts}
    def key(d: Decision):
        r = by_id.get(d.receipt_id or "")
        if r is None:
            return (1, date.max, 99, "")  # 정액 행: 뒤에, 원래 순서 유지(stable sort)
        return (0, _receipt_day(r) or date.max, _CAT_ORDER[r.category], r.receipt_id)
    return sorted(decisions, key=key)

def build_markdown(trip: TripConfig, law: LawSnapshot, receipts: list[Receipt], decisions: list[Decision],
                   image_names: dict[str, list[str]], version: int | None = None) -> str:
    by_id = {r.receipt_id: r for r in receipts}
    ordered = order_decisions(decisions, receipts)
    t = totals(decisions)
    period = f"{_kdate(trip.start_date)}~{_kdate(trip.end_date)}"
    overview = [["출장자", f"{trip.traveler_name} {trip.position}".strip()], ["여비 구분", trip.grade or "미확정"],
                ["근무지 / 출장지", f"{trip.workplace_region or '-'} / {trip.destination_region or '-'}"],
                ["출장기간", f"{period} ({trip.days or '-'}일)"], ["출장목적", trip.purpose or "-"], ["영수증", f"{len(receipts)}건"]]
    if version is not None:
        overview.append(["문서 버전", f"v{version}"])
    md = ["# 출장여비 영수증 증빙내역서",
          f"> {period} {trip.destination_region or '국내'} 출장 영수증 증빙내역을 공무원 여비 규정에 따라 검토하여 여비를 정산하고자 함", ""]
    if trip.proposed:
        md += [f"※ 출장 정보는 영수증으로 자동 제안한 값입니다(근거: {'; '.join(trip.proposal_basis) or '없음'}). trip.yaml로 확정해 주세요.", ""]
    md += ["## 출장 개요", md_table(["항목", "내용"], overview), "", "## 지급대상 요약"]
    grp: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for d in ordered:
        grp[d.item][0] += d.claimed_amount; grp[d.item][1] += d.approved_amount
    rows = [[k, fmt_won(v[0]), fmt_won(v[1])] for k, v in grp.items()] + [["합계", fmt_won(t["claimed"]), fmt_won(t["approved"])]]
    md += [md_table(["구분", "청구액", "인정액"], rows), "", f"※ 확인필요 항목 청구액 합계: {fmt_won(t['review'])}원 (인정액 미포함)", "",
           "## 영수증별 상세"]
    detail = []
    for i, d in enumerate(ordered, start=1):
        r = by_id.get(d.receipt_id or "")
        day = _receipt_day(r) if r else None
        detail.append([str(i), _kdate(day) if day else ("정액" if r is None else "미상"), d.item, (r.merchant or "-") if r else "-",
                       (r.approval_no or "-") if r else "-", fmt_won(d.claimed_amount), fmt_won(d.approved_amount), d.verdict.value, ", ".join(d.basis) or "-"])
    detail.append(["합계", "", "", "", "", fmt_won(t["claimed"]), fmt_won(t["approved"]), "", ""])
    md += [md_table(DETAIL_HEADERS, detail), "", "## 적용 규정",
           f"### 공무원 여비 규정(대통령령, {_kdate(law.promulgated)} 개정, {_kdate(law.effective)} 시행)"]
    for g, rt in law.rate_tables.items():
        caps = ", ".join(f"{k} {fmt_won(v)}" for k, v in rt.lodging_caps.items()) if rt.lodging_caps else "실비"
        md.append(f"- {g}: 철도 {rt.rail}, 일비 {fmt_won(rt.daily_allowance)}/일, 식비 {fmt_won(rt.meal_allowance)}/일, 숙박비 {caps}")
    md += ["- 제16조: 숙박비는 숙박한 밤의 수, 일비·식비는 여행일수 기준. 상한 초과 시 부득이한 사유가 있으면 상한액의 10분의 3 이내 추가지급 가능",
           "- 제18조: 근무지 내 국내출장은 정액(4시간 이상 2만원, 미만 1만원)", "", "## 확인필요 사항"]
    reviews = [d for d in ordered if d.verdict is Verdict.REVIEW]
    md += [f"- {d.item}({d.receipt_id or '정액'}): {'; '.join(d.reasons)}" for d in reviews] or ["- 없음"]
    md += ["", "## 붙임"]
    for d in ordered:
        names = image_names.get(d.receipt_id or "", [])
        if names:
            md += [f"### {d.item} 영수증 ({d.receipt_id})"] + [f"![]({n})" for n in names] + [""]
    return "\n".join(md).rstrip() + "\n"
```
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_report.py` → `3 passed`
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/report.py tests/test_report.py && git commit -m "feat: 증빙내역서 마크다운 빌더(정렬·버전·자동 제안 표기)"`

---

### Task 11: export (붙임 축소 · kordoc 생성 · 역파싱 합계 검증)
**Files:** Create `src/receipt_evidence/export.py`, `tests/test_export.py`
**Interfaces:** Consumes `ToolCaller`, `TripConfig`, `Receipt`, `ReceiptImage`, `DETAIL_HEADERS`, `html_table_rows`(Task 7). Produces:
- `ATTACHMENT_MAX_SIDE = 1600`
- `prepare_attachments(images: list[ReceiptImage], receipts: list[Receipt], dst: Path, max_side: int = ATTACHMENT_MAX_SIDE) -> dict[str, list[str]]`: receipt_id → 붙임 JPEG 파일명 목록(원본의 모든 쪽, 쪽 순서)
- `parse_md_tables(md: str) -> list[list[list[str]]]` (GFM 표 + HTML `<table>`), `approved_total_from_md(md: str) -> int`
- `export_hwpx(caller, markdown: str, out_path: Path, trip: TripConfig, image_dir: Path) -> Path`
- `verify_hwpx(caller, hwpx_path: Path, expected_approved: int) -> dict` (`ok`, `parsed_total`, `expected`, `error`)

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/test_export.py
from pathlib import Path
import pytest
from PIL import Image
from receipt_evidence.export import approved_total_from_md, export_hwpx, parse_md_tables, prepare_attachments, verify_hwpx
from receipt_evidence.mcp_client import FakeToolCaller
from receipt_evidence.models import Receipt, ReceiptImage

MD = "# 제목\n\n## 영수증별 상세\n| 연번 | 일자 | 구분 | 가맹점 | 승인번호 | 결제액 | 인정액 | 판정 | 근거 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n| 1 | 2026. 7. 9. | 철도운임 | 한국철도공사 | 55431218 | 48,200 | 48,200 | 지급 | 별표2 |\n| 합계 |  |  |  |  | 48,200 | 48,200 |  |  |\n"

def test_parse_tables_and_total():
    tables = parse_md_tables(MD)
    assert tables[0][0][0] == "연번" and tables[0][-1][0] == "합계" and approved_total_from_md(MD) == 48200

def test_parse_html_table_from_kordoc():
    head = "".join(f"<th>{h}</th>" for h in ["연번", "일자", "구분", "가맹점", "승인번호", "결제액", "인정액", "판정", "근거"])
    total = "<td>합계</td>" + "<td></td>" * 4 + "<td>48,200</td><td>48,200</td>" + "<td></td>" * 2
    assert approved_total_from_md(f"본문\n<table><tr>{head}</tr><tr>{total}</tr></table>\n") == 48200

def test_export_calls_generate_with_preset(tmp_path, trip):
    calls = FakeToolCaller({"generate_document": lambda a: Path(a["output_path"]).write_bytes(b"PK") and "ok"})
    out = export_hwpx(calls, MD, tmp_path / "e.hwpx", trip, tmp_path)
    name, args = calls.calls[0]
    assert out.exists() and name == "generate_document" and args["preset"] == "보고서" and args["image_dir"] == str(tmp_path)
    assert args["approval"] == ["담당", "팀장"] and args["org"] == trip.org

def test_verify_hwpx_compares_total(tmp_path):
    ok = verify_hwpx(FakeToolCaller({"parse_document": lambda a: MD}), tmp_path / "e.hwpx", 48200)
    bad = verify_hwpx(FakeToolCaller({"parse_document": lambda a: MD}), tmp_path / "e.hwpx", 1)
    assert ok["ok"] and ok["parsed_total"] == 48200 and not bad["ok"]

def test_export_error_raises(tmp_path, trip):
    with pytest.raises(RuntimeError):
        export_hwpx(FakeToolCaller({}), MD, tmp_path / "e.hwpx", trip, tmp_path)

def test_prepare_attachments_downscales_and_groups_pages(tmp_path):
    imgs = []
    for page in (1, 2):
        p = tmp_path / f"pdf-p{page}.png"; Image.new("RGB", (2400, 3200), "white").save(p)
        imgs.append(ReceiptImage(image_id=f"pdf-p{page}", source_path="stay.pdf", page=page, png_path=str(p), sha256="pdf", width=2400, height=3200))
    names = prepare_attachments(list(reversed(imgs)), [Receipt(receipt_id="pdf-p1", image_id="pdf-p1")], tmp_path / "att")
    assert names == {"pdf-p1": ["pdf-p1.jpg", "pdf-p2.jpg"]}
    with Image.open(tmp_path / "att" / "pdf-p2.jpg") as im:
        assert max(im.size) == 1600
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_export.py` → `ModuleNotFoundError: No module named 'receipt_evidence.export'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/export.py
from __future__ import annotations
import re
from datetime import date
from pathlib import Path
from PIL import Image
from .law import html_table_rows
from .mcp_client import ToolCaller
from .models import Receipt, ReceiptImage, TripConfig
from .report import DETAIL_HEADERS

ATTACHMENT_MAX_SIDE = 1600

def prepare_attachments(images: list[ReceiptImage], receipts: list[Receipt], dst: Path, max_side: int = ATTACHMENT_MAX_SIDE) -> dict[str, list[str]]:
    dst.mkdir(parents=True, exist_ok=True)
    pages: dict[str, list[ReceiptImage]] = {}
    for img in sorted(images, key=lambda i: (i.sha256, i.page)):
        pages.setdefault(img.sha256, []).append(img)
    sha_of = {i.image_id: i.sha256 for i in images}
    out: dict[str, list[str]] = {}
    for r in receipts:
        names = []
        for img in pages.get(sha_of.get(r.image_id, ""), []):
            name = f"{img.image_id}.jpg"
            with Image.open(img.png_path) as im:
                small = im.convert("RGB")
                small.thumbnail((max_side, max_side))
                small.save(dst / name, "JPEG", quality=85)
            names.append(name)
        out[r.receipt_id] = names
    return out

def parse_md_tables(md: str) -> list[list[list[str]]]:
    tables, cur = [], []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s[1:-1].split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            cur.append(cells)
        elif cur:
            tables.append(cur); cur = []
    if cur:
        tables.append(cur)
    for chunk in re.findall(r"<table.*?</table>", md, flags=re.S | re.I):
        rows = [r for r in html_table_rows(chunk) if r]
        if rows:
            tables.append(rows)
    return tables

def approved_total_from_md(md: str) -> int:
    for t in parse_md_tables(md):
        if t and t[0][:2] == DETAIL_HEADERS[:2]:
            col = t[0].index("인정액")
            row = next((r for r in t if r and r[0] == "합계"), None)
            if row:
                return int(re.sub(r"[^\d]", "", row[col]) or 0)
    raise ValueError("역파싱 결과에서 영수증별 상세 표의 합계 행을 찾지 못함")

def export_hwpx(caller: ToolCaller, markdown: str, out_path: Path, trip: TripConfig, image_dir: Path) -> Path:
    today = date.today()
    args = {"markdown": markdown, "output_path": str(out_path), "preset": "보고서", "image_dir": str(image_dir),
            "date": f"{today.year}. {today.month}. {today.day}.", "end_mark": False,
            "report_info": f"({today.year}. {today.month}. {today.day}., {trip.dept or trip.org or '소속 미기재'} {trip.traveler_name})"}
    if trip.org:
        args["org"] = trip.org
    if trip.approval:
        args["approval"] = trip.approval[:6]
    res = caller.call_many([("generate_document", args)])[0]
    if res.is_error or not out_path.exists():
        raise RuntimeError(f"generate_document 실패: {res.text}")
    return out_path

def verify_hwpx(caller: ToolCaller, hwpx_path: Path, expected_approved: int) -> dict:
    res = caller.call_many([("parse_document", {"file_path": str(hwpx_path)})])[0]
    if res.is_error:
        return {"ok": False, "parsed_total": None, "expected": expected_approved, "error": res.text}
    try:
        parsed = approved_total_from_md(res.text)
    except ValueError as e:
        return {"ok": False, "parsed_total": None, "expected": expected_approved, "error": str(e)}
    return {"ok": parsed == expected_approved, "parsed_total": parsed, "expected": expected_approved, "error": None}
```
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_export.py` → `6 passed`
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/export.py tests/test_export.py && git commit -m "feat: 붙임 축소·kordoc HWPX 생성·역파싱 합계 검증"`

---

### Task 12: versioning (fingerprint · latest.json · 변경 내역)
**Files:** Create `src/receipt_evidence/versioning.py`, `tests/test_versioning.py`
**Interfaces:** Consumes `Receipt, TripConfig, LawSnapshot, Decision, RULES_VERSION, totals, fmt_won`. Produces `fingerprint(receipts, trip, law) -> str`, `read_latest(trip_out: Path) -> dict | None`, `plan_version(trip_out: Path, fp: str, *, force: bool = False) -> tuple[int, bool]`(버전 번호, 새로 만들지), `write_latest(trip_out: Path, version: int, fp: str) -> None`, `load_decisions(path: Path) -> list[Decision]`, `diff_markdown(prev: list[Decision], new: list[Decision], prev_version: int, new_version: int) -> str`

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/test_versioning.py
import json
from datetime import date
from pathlib import Path
from receipt_evidence.models import Receipt
from receipt_evidence.rules import decide_all
from receipt_evidence.versioning import diff_markdown, fingerprint, load_decisions, plan_version, read_latest, write_latest

GOLD = [Receipt(**d) for d in json.loads((Path(__file__).parent / "fixtures/golden/receipts.json").read_text(encoding="utf-8"))]

def test_fingerprint_changes_only_with_decision_inputs(trip, law_snapshot):
    fp = fingerprint(GOLD, trip, law_snapshot)
    assert fp == fingerprint(list(reversed(GOLD)), trip, law_snapshot)
    assert fp == fingerprint([g.model_copy(update={"transcript_path": "elsewhere.txt", "confidence": 0.5}) for g in GOLD], trip, law_snapshot)
    assert fp != fingerprint(GOLD[:2], trip, law_snapshot)
    assert fp != fingerprint(GOLD, trip.model_copy(update={"lodging_region": "서울"}), law_snapshot)
    assert fp != fingerprint(GOLD, trip, law_snapshot.model_copy(update={"mst": "999999"}))

def test_plan_version_flow(tmp_path):
    assert plan_version(tmp_path, "a") == (1, True)
    write_latest(tmp_path, 1, "a")
    assert plan_version(tmp_path, "a") == (1, False) and plan_version(tmp_path, "a", force=True) == (2, True)
    assert plan_version(tmp_path, "b") == (2, True) and read_latest(tmp_path) == {"version": 1, "fingerprint": "a"}

def test_diff_markdown_lists_added_and_totals(trip, law_snapshot, tmp_path):
    v1 = decide_all(GOLD[:2], trip, law_snapshot)
    fixed_stay = GOLD[2].model_copy(update={"service_date": date(2026, 7, 9), "region": "서울"})
    v2 = decide_all(GOLD[:2] + [fixed_stay], trip, law_snapshot)
    md = diff_markdown(v1, v2, 1, 2)
    assert "# 변경 내역 v1 → v2" in md and "숙박비(stay)" in md and "인정 196,400 → 296,400" in md and "## 삭제\n- 없음" in md
    (tmp_path / "d.json").write_text(json.dumps([d.model_dump(mode="json") for d in v2]), encoding="utf-8")
    assert load_decisions(tmp_path / "d.json") == v2 and load_decisions(tmp_path / "none.json") == []
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_versioning.py` → `ModuleNotFoundError: No module named 'receipt_evidence.versioning'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/versioning.py
from __future__ import annotations
import hashlib, json
from pathlib import Path
from .models import Decision, LawSnapshot, Receipt, TripConfig
from .report import fmt_won
from .rules import RULES_VERSION, totals

_RECEIPT_FIELDS = {"receipt_id", "sha256", "category", "merchant", "business_no", "amount", "paid_at", "service_date", "service_end_date",
                   "origin", "destination", "seat_class", "train_no", "approval_no", "region", "nights", "warnings"}

def fingerprint(receipts: list[Receipt], trip: TripConfig, law: LawSnapshot) -> str:
    payload = {
        "receipts": sorted((r.model_dump(mode="json", include=_RECEIPT_FIELDS) for r in receipts), key=lambda d: d["receipt_id"]),
        "trip": trip.model_dump(mode="json"),
        "law": [law.mst, law.effective.isoformat()],
        "rules": RULES_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

def read_latest(trip_out: Path) -> dict | None:
    p = trip_out / "latest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

def plan_version(trip_out: Path, fp: str, *, force: bool = False) -> tuple[int, bool]:
    latest = read_latest(trip_out)
    if latest is None:
        return 1, True
    if latest["fingerprint"] == fp and not force:
        return int(latest["version"]), False
    return int(latest["version"]) + 1, True

def write_latest(trip_out: Path, version: int, fp: str) -> None:
    trip_out.mkdir(parents=True, exist_ok=True)
    (trip_out / "latest.json").write_text(json.dumps({"version": version, "fingerprint": fp}, ensure_ascii=False, indent=2), encoding="utf-8")

def load_decisions(path: Path) -> list[Decision]:
    if not path.exists():
        return []
    return [Decision.model_validate(d) for d in json.loads(path.read_text(encoding="utf-8"))]

def diff_markdown(prev: list[Decision], new: list[Decision], prev_version: int, new_version: int) -> str:
    key = lambda d: d.receipt_id or f"정액:{d.item}"
    p, n = {key(d): d for d in prev}, {key(d): d for d in new}
    added = [n[k] for k in n if k not in p]
    removed = [p[k] for k in p if k not in n]
    changed = [(p[k], n[k]) for k in n if k in p and (p[k].verdict, p[k].approved_amount, p[k].claimed_amount) != (n[k].verdict, n[k].approved_amount, n[k].claimed_amount)]
    tp, tn = totals(prev), totals(new)
    lines = [f"# 변경 내역 v{prev_version} → v{new_version}", "", "## 추가"]
    lines += [f"- {d.item}({key(d)}): 청구 {fmt_won(d.claimed_amount)}, 인정 {fmt_won(d.approved_amount)}, {d.verdict.value}" for d in added] or ["- 없음"]
    lines += ["", "## 삭제"]
    lines += [f"- {d.item}({key(d)}): 청구 {fmt_won(d.claimed_amount)}" for d in removed] or ["- 없음"]
    lines += ["", "## 판정 변경"]
    lines += [f"- {b.item}({key(b)}): {a.verdict.value} {fmt_won(a.approved_amount)} → {b.verdict.value} {fmt_won(b.approved_amount)}" for a, b in changed] or ["- 없음"]
    lines += ["", "## 합계", f"- 청구 {fmt_won(tp['claimed'])} → {fmt_won(tn['claimed'])}", f"- 인정 {fmt_won(tp['approved'])} → {fmt_won(tn['approved'])}",
              f"- 확인필요 {fmt_won(tp['review'])} → {fmt_won(tn['review'])}"]
    return "\n".join(lines) + "\n"
```
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_versioning.py` → `3 passed`
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/versioning.py tests/test_versioning.py && git commit -m "feat: fingerprint 기반 문서 버전 관리와 변경 내역"`

---

### Task 13: pipeline (출장별 prepare/finalize · 일괄 처리 · 요약)
**Files:** Create `src/receipt_evidence/pipeline.py`, `tests/helpers.py`, `tests/test_pipeline.py`
**Interfaces:** Consumes 앞 태스크의 공개 함수 전부. Produces:
- `Clients(vlm: VlmClient, law: ToolCaller, doc: ToolCaller)`, `RunOptions(workers: int = 1, new_version: bool = False, refresh_law: bool = False, today: date = 오늘)`
- `TripWork(job: TripJob, work_dir: Path, images: list[ReceiptImage], receipts: list[Receipt], cache_hits: int, cache_misses: int)`
- `trip_out_dir(out_dir: Path, job: TripJob) -> Path`
- `prepare_trip(job, images, out_dir, clients, cache, opts) -> TripWork` (extract → validate → overrides)
- `finalize_trip(work, law, clients, out_dir, opts, run_id) -> PipelineResult` (trip 해석 → 교차검사 → 판정 → fingerprint → 새 버전이면 붙임·보고서·changes.md·HWPX·검증)
- `write_summary(out_dir: Path, run_id: str, results: list[PipelineResult], warnings: list[str]) -> tuple[Path, Path]`
- `run_batch(data_dir: Path, out_dir: Path, clients: Clients, *, travelers: list[str] | None = None, trips: list[str] | None = None, opts: RunOptions | None = None, run_id: str | None = None) -> BatchResult`

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/helpers.py
"""파이프라인 테스트용: 단색 영수증 이미지를 만들고, 이미지 색으로 영수증을 식별해 답하는 가짜 VLM."""
import base64, io, json, threading
from pathlib import Path
from PIL import Image

_FIELDS = ("merchant", "business_no", "paid_at", "service_date", "service_end_date", "origin", "destination", "seat_class",
           "train_no", "card_masked", "payer_name", "region", "nights")

def color_png(path: Path, rgb: tuple[int, int, int], size=(240, 480)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, rgb).save(path, "PNG")

def spec(category: str, amount: int, approval_no: str, **kw) -> dict:
    return {k: None for k in _FIELDS} | {"category": category, "amount": amount, "approval_no": approval_no} | kw

class ColorVlm:
    def __init__(self, specs: dict[tuple[int, int, int], dict], healthy: bool = True):
        self.specs = specs
        self._healthy = healthy
        self.calls = 0
        self._lock = threading.Lock()

    def healthy(self) -> bool:
        return self._healthy

    def chat(self, messages, *, json_schema=None, max_tokens=2048, schema_mode="json_schema"):
        with self._lock:
            self.calls += 1
        url = next(c["image_url"]["url"] for c in messages[-1]["content"] if c["type"] == "image_url")
        rgb = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB").getpixel((5, 5))
        s = self.specs[tuple(rgb)]
        if json_schema is None:
            return f"결제금액 {s['amount']:,}원 승인번호 {s['approval_no']} {s.get('business_no') or ''}"
        return json.dumps(s, ensure_ascii=False)
```
```python
# tests/test_pipeline.py
import json, shutil
from datetime import date, timedelta
from pathlib import Path
import pytest
from helpers import ColorVlm, color_png, spec
from receipt_evidence.mcp_client import FakeToolCaller
from receipt_evidence.models import Verdict
from receipt_evidence.pipeline import Clients, RunOptions, run_batch

TRAVELER = "grade: 제2호\nworkplace_region: 나주\napproval: [담당, 팀장]\n"

def _law(t):
    return FakeToolCaller({"search_law": lambda a: t["search_law.txt"],
                           "get_annexes": lambda a: t["annex1.html"] if a["annexNo"] == "1" else t["annex2.html"],
                           "get_law_text": lambda a: t["jo" + a["jo"][1:-1] + ".txt"]})

def _doc(fail_on: str | None = None):
    def generate(a):
        if fail_on and fail_on in a["output_path"]:
            raise RuntimeError("kordoc 실패 재현")
        Path(a["output_path"]).write_bytes(b"PK")
        return "ok"
    return FakeToolCaller({"generate_document": generate,
                           "parse_document": lambda a: (Path(a["file_path"]).parent / "report.md").read_text(encoding="utf-8")})

def _rail(n: int, day: date, o: str, d: str) -> dict:
    return spec("철도", 48200, f"7{n:07d}", merchant="한국철도공사", business_no="314-82-10024", service_date=day.isoformat(),
                paid_at=f"{day.isoformat()} 09:00", origin=o, destination=d, seat_class="일반실", train_no=f"KTX {100 + n}")

def test_single_trip_versions_and_incremental_submission(tmp_path, law_fixture_text):
    data, out = tmp_path / "data", tmp_path / "out"
    trav = data / "정백철"; trip_dir = trav / "2026-07-09_서울"; trip_dir.mkdir(parents=True)
    (trav / "traveler.yaml").write_text(TRAVELER, encoding="utf-8")
    (trip_dir / "trip.yaml").write_text("start_date: 2026-07-09\nend_date: 2026-07-10\ndestination_region: 서울\nroute_stations: [나주, 용산]\n", encoding="utf-8")
    specs = {(10, 20, 30): _rail(1, date(2026, 7, 9), "나주", "용산"), (40, 50, 60): _rail(2, date(2026, 7, 10), "용산", "나주"),
             (70, 80, 90): spec("숙박", 100000, "68325420", merchant="(주)예시숙박", service_date="2026-07-09", region="서울", nights=1)}
    color_png(trip_dir / "k1.png", (10, 20, 30)); color_png(trip_dir / "k2.png", (40, 50, 60))
    vlm = ColorVlm(specs)
    clients = Clients(vlm=vlm, law=_law(law_fixture_text), doc=_doc())

    r1 = run_batch(data, out, clients, run_id="b1").results[0]
    assert (r1.version, r1.skipped, r1.cache_misses, r1.verify_ok) == (1, False, 2, True)
    assert r1.totals == {"claimed": 96400, "approved": 196400, "review": 0}
    calls = vlm.calls

    r2 = run_batch(data, out, clients, run_id="b2").results[0]
    assert (r2.version, r2.skipped, r2.cache_hits) == (1, True, 2) and vlm.calls == calls

    color_png(trip_dir / "stay.png", (70, 80, 90))
    r3 = run_batch(data, out, clients, run_id="b3").results[0]
    assert (r3.version, r3.skipped, r3.cache_hits, r3.cache_misses) == (2, False, 2, 1)
    assert r3.totals["approved"] == 296400 and vlm.calls == calls + 2
    assert "v1 → v2" in Path(r3.changes_md_path).read_text(encoding="utf-8") and "숙박비" in Path(r3.changes_md_path).read_text(encoding="utf-8")
    trip_out = out / "정백철" / "2026-07-09_서울"
    assert json.loads((trip_out / "latest.json").read_text())["version"] == 2 and (trip_out / "v1" / "evidence.hwpx").exists()
    assert len(list((trip_out / "v2" / "attachments").glob("*.jpg"))) == 3 and (out / "summary-b3.md").exists()

def test_batch_travelers_trips_scale_and_cross_trip_duplicate(tmp_path, law_fixture_text):
    data, out = tmp_path / "data", tmp_path / "out"
    plan = [("정백철", "2026-07-09_서울", date(2026, 7, 9), "start_date: 2026-07-09\nend_date: 2026-07-10\ndestination_region: 서울\n"),
            ("정백철", "2026-08-03_부산", date(2026, 8, 3), "start_date: 2026-08-03\nend_date: 2026-08-04\ndestination_region: 부산\n"),
            ("홍길동", "2026-07-20_대전", date(2026, 7, 20), "start_date: 2026-07-20\nend_date: 2026-07-21\ndestination_region: 대전\n"),
            ("홍길동", "2026-09-01_광주", date(2026, 9, 1), None)]
    specs, n = {}, 0
    for traveler, trip_id, day, trip_yaml in plan:
        trip_dir = data / traveler / trip_id; trip_dir.mkdir(parents=True)
        (data / traveler / "traveler.yaml").write_text(TRAVELER, encoding="utf-8")
        if trip_yaml:
            (trip_dir / "trip.yaml").write_text(trip_yaml, encoding="utf-8")
        for k in range(3):
            n += 1
            rgb = (n * 7 % 256, 100, 200 - n)
            specs[rgb] = _rail(n, day + timedelta(days=k % 2), "나주", "서울역")
            color_png(trip_dir / f"r{k}.png", rgb)
    shutil.copy(data / "정백철" / "2026-07-09_서울" / "r0.png", data / "홍길동" / "2026-07-20_대전" / "dup.png")

    res = run_batch(data, out, Clients(vlm=ColorVlm(specs), law=_law(law_fixture_text), doc=_doc()), run_id="b1", opts=RunOptions(workers=3))
    by = {(r.traveler, r.trip_id): r for r in res.results}
    assert len(res.results) == 4 and all(r.error is None and r.version == 1 and r.verify_ok for r in res.results)
    assert sum(r.cache_misses for r in res.results) == 12 and sum(r.cache_hits for r in res.results) == 1
    assert sum(len(r.receipts) for r in res.results) == 13
    dup = sorted(k for k, r in by.items() if any("DUP_ACROSS_TRIPS" in x.warnings for x in r.receipts))
    assert dup == [("정백철", "2026-07-09_서울"), ("홍길동", "2026-07-20_대전")]
    proposed = by[("홍길동", "2026-09-01_광주")]
    assert proposed.trip.proposed and all(d.verdict is Verdict.REVIEW for d in proposed.decisions if d.receipt_id is None)
    assert (out / "홍길동" / "2026-09-01_광주" / "work" / "trip.proposed.yaml").exists()
    assert Path(res.summary_md_path).read_text(encoding="utf-8").count("| v1 |") == 4 and Path(res.summary_json_path).exists()

def test_no_trip_folders_raises_with_guidance(tmp_path, law_fixture_text):
    data = tmp_path / "data"; data.mkdir(); color_png(data / "loose.png", (1, 2, 3))
    with pytest.raises(ValueError, match="출장자"):
        run_batch(data, tmp_path / "out", Clients(vlm=ColorVlm({}), law=_law(law_fixture_text), doc=_doc()))

def test_unhealthy_vlm_only_matters_on_cache_miss(tmp_path, law_fixture_text):
    data, out = tmp_path / "data", tmp_path / "out"
    trip_dir = data / "정백철" / "2026-07-09_서울"; trip_dir.mkdir(parents=True)
    specs = {(10, 20, 30): _rail(1, date(2026, 7, 9), "나주", "용산"), (40, 50, 60): _rail(2, date(2026, 7, 10), "용산", "나주")}
    color_png(trip_dir / "k1.png", (10, 20, 30))
    run_batch(data, out, Clients(vlm=ColorVlm(specs), law=_law(law_fixture_text), doc=_doc()), run_id="a")
    down = ColorVlm(specs, healthy=False)
    assert run_batch(data, out, Clients(vlm=down, law=_law(law_fixture_text), doc=_doc()), run_id="b").results[0].skipped
    color_png(trip_dir / "k2.png", (40, 50, 60))
    with pytest.raises(RuntimeError, match="llama-server"):
        run_batch(data, out, Clients(vlm=down, law=_law(law_fixture_text), doc=_doc()), run_id="c")

def test_one_trip_failure_does_not_stop_batch(tmp_path, law_fixture_text):
    data, out = tmp_path / "data", tmp_path / "out"
    specs = {}
    for trip_id, rgb, day in (("2026-07-09_서울", (10, 20, 30), date(2026, 7, 9)), ("2026-08-03_부산", (40, 50, 60), date(2026, 8, 3))):
        d = data / "정백철" / trip_id; d.mkdir(parents=True); color_png(d / "k.png", rgb)
        specs[rgb] = _rail(rgb[0], day, "나주", "부산")
    res = run_batch(data, out, Clients(vlm=ColorVlm(specs), law=_law(law_fixture_text), doc=_doc(fail_on="2026-08-03_부산")), run_id="x")
    by = {r.trip_id: r for r in res.results}
    assert by["2026-07-09_서울"].error is None and "kordoc 실패 재현" in by["2026-08-03_부산"].error
    assert "오류" in Path(res.summary_md_path).read_text(encoding="utf-8")
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_pipeline.py` → `ModuleNotFoundError: No module named 'receipt_evidence.pipeline'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/pipeline.py
from __future__ import annotations
import json, logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from .cache import ExtractCache
from .export import export_hwpx, prepare_attachments, verify_hwpx
from .extract import extract_receipts
from .ingest import ingest
from .law import get_law_snapshot
from .mcp_client import ToolCaller
from .models import BatchResult, LawSnapshot, PipelineResult, Receipt, ReceiptImage, TripConfig
from .report import build_markdown, fmt_won, md_table
from .rules import apply_cross_checks, decide_all, mark_cross_trip_duplicates, review_items, totals
from .validate import validate_all
from .versioning import diff_markdown, fingerprint, load_decisions, plan_version, write_latest
from .vlm import VlmClient
from .workspace import TripJob, apply_overrides, discover, dump_trip_yaml, load_overrides, load_traveler, resolve_trip

log = logging.getLogger("receipt_evidence")

@dataclass
class Clients:
    vlm: VlmClient
    law: ToolCaller
    doc: ToolCaller

@dataclass
class RunOptions:
    workers: int = 1
    new_version: bool = False
    refresh_law: bool = False
    today: date = field(default_factory=date.today)

@dataclass
class TripWork:
    job: TripJob
    work_dir: Path
    images: list[ReceiptImage]
    receipts: list[Receipt]
    cache_hits: int
    cache_misses: int

def _dump(path: Path, items: list) -> None:
    path.write_text(json.dumps([i.model_dump(mode="json") for i in items], ensure_ascii=False, indent=2), encoding="utf-8")

def _attach_run_log(path: Path) -> None:
    for h in list(log.handlers):
        log.removeHandler(h); h.close()
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh); log.setLevel(logging.INFO)

def trip_out_dir(out_dir: Path, job: TripJob) -> Path:
    return out_dir / job.traveler / job.trip_id

def prepare_trip(job: TripJob, images: list[ReceiptImage], out_dir: Path, clients: Clients, cache: ExtractCache, opts: RunOptions) -> TripWork:
    work = trip_out_dir(out_dir, job) / "work"
    hits, misses = cache.hits, cache.misses
    receipts = extract_receipts(clients.vlm, images, work, cache=cache, workers=opts.workers)
    transcripts = {r.receipt_id: Path(r.transcript_path).read_text(encoding="utf-8") for r in receipts}
    receipts = apply_overrides(validate_all(receipts, transcripts), load_overrides(job.trip_dir))
    _dump(work / "receipts.json", receipts)
    log.info("prepare %s/%s: receipts=%d cache_hits=%d cache_misses=%d", job.traveler, job.trip_id, len(receipts),
             cache.hits - hits, cache.misses - misses)
    return TripWork(job, work, images, receipts, cache.hits - hits, cache.misses - misses)

def finalize_trip(work: TripWork, law: LawSnapshot, clients: Clients, out_dir: Path, opts: RunOptions, run_id: str) -> PipelineResult:
    job = work.job
    trip_out = trip_out_dir(out_dir, job)
    trip = resolve_trip(job, load_traveler(job.traveler_dir), work.receipts)
    if trip.proposed:
        (work.work_dir / "trip.proposed.yaml").write_text(dump_trip_yaml(trip), encoding="utf-8")
    receipts = apply_cross_checks(work.receipts, trip)
    decisions = decide_all(receipts, trip, law)
    t = totals(decisions)
    fp = fingerprint(receipts, trip, law)
    version, create = plan_version(trip_out, fp, force=opts.new_version)
    vdir = trip_out / f"v{version}"
    common = dict(run_id=run_id, traveler=job.traveler, trip_id=job.trip_id, trip=trip, law_mst=law.mst, law_effective=law.effective,
                  receipts=receipts, decisions=decisions, totals=t, review_items=review_items(decisions, receipts), version=version,
                  cache_hits=work.cache_hits, cache_misses=work.cache_misses)
    if not create:
        verify = json.loads((vdir / "verify.json").read_text(encoding="utf-8")) if (vdir / "verify.json").exists() else {}
        log.info("finalize %s/%s: 변경 없음 v%d", job.traveler, job.trip_id, version)
        return PipelineResult(**common, report_md_path=str(vdir / "report.md"), hwpx_path=str(vdir / "evidence.hwpx"), skipped=True,
                              verify_ok=verify.get("ok"))
    vdir.mkdir(parents=True, exist_ok=True)
    names = prepare_attachments(work.images, receipts, vdir / "attachments")
    md = build_markdown(trip, law, receipts, decisions, names, version=version)
    (vdir / "report.md").write_text(md, encoding="utf-8")
    _dump(vdir / "decisions.json", decisions)
    (vdir / "fingerprint.json").write_text(json.dumps({"fingerprint": fp}, indent=2), encoding="utf-8")
    changes: Path | None = None
    if version > 1:
        changes = vdir / "changes.md"
        changes.write_text(diff_markdown(load_decisions(trip_out / f"v{version - 1}" / "decisions.json"), decisions, version - 1, version), encoding="utf-8")
    hwpx = export_hwpx(clients.doc, md, vdir / "evidence.hwpx", trip, vdir / "attachments")
    verify = verify_hwpx(clients.doc, hwpx, t["approved"])
    (vdir / "verify.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2), encoding="utf-8")
    write_latest(trip_out, version, fp)
    result = PipelineResult(**common, report_md_path=str(vdir / "report.md"), hwpx_path=str(hwpx),
                            changes_md_path=str(changes) if changes else None, verify_ok=bool(verify["ok"]))
    (vdir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    log.info("finalize %s/%s: v%d approved=%d review=%d verify=%s", job.traveler, job.trip_id, version, t["approved"],
             len(result.review_items), verify["ok"])
    return result

def _failed(work: TripWork, law: LawSnapshot, run_id: str, err: Exception) -> PipelineResult:
    return PipelineResult(run_id=run_id, traveler=work.job.traveler, trip_id=work.job.trip_id,
                          trip=TripConfig(traveler_name=work.job.traveler, trip_id=work.job.trip_id), law_mst=law.mst,
                          law_effective=law.effective, receipts=work.receipts, decisions=[], totals={"claimed": 0, "approved": 0, "review": 0},
                          review_items=[], report_md_path="", cache_hits=work.cache_hits, cache_misses=work.cache_misses,
                          error=f"{type(err).__name__}: {err}")

def write_summary(out_dir: Path, run_id: str, results: list[PipelineResult], warnings: list[str]) -> tuple[Path, Path]:
    rows = []
    for r in results:
        state = "오류" if r.error else ("변경 없음" if r.skipped else "새 버전")
        check = "-" if r.verify_ok is None else ("통과" if r.verify_ok else "불일치")
        rows.append([r.traveler, r.trip_id, f"v{r.version}" if r.version else "-", state, f"{len(r.receipts)}건",
                     fmt_won(r.totals.get("claimed", 0)), fmt_won(r.totals.get("approved", 0)), f"{len(r.review_items)}건", check,
                     r.error or r.hwpx_path or "-"])
    md = [f"# 일괄 정산 요약 ({run_id})", "",
          md_table(["출장자", "출장", "버전", "상태", "영수증", "청구", "인정", "확인필요", "합계 검증", "문서 또는 오류"], rows), ""]
    if warnings:
        md += ["## 처리하지 않은 파일·폴더", *[f"- {w}" for w in warnings], ""]
    md_path, json_path = out_dir / f"summary-{run_id}.md", out_dir / f"summary-{run_id}.json"
    md_path.write_text("\n".join(md), encoding="utf-8")
    json_path.write_text(json.dumps({"run_id": run_id, "warnings": warnings,
                                     "results": [r.model_dump(mode="json", exclude={"receipts", "decisions"}) for r in results]},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path

def run_batch(data_dir: Path, out_dir: Path, clients: Clients, *, travelers: list[str] | None = None, trips: list[str] | None = None,
              opts: RunOptions | None = None, run_id: str | None = None) -> BatchResult:
    opts = opts or RunOptions()
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    _attach_run_log(out_dir / f"run-{run_id}.log")
    jobs, warnings = discover(data_dir, travelers, trips)
    if not jobs:
        raise ValueError("처리할 출장 폴더가 없어요. data/<출장자>/<출장>/ 구조로 영수증을 넣어 주세요")
    cache = ExtractCache(out_dir / ".cache")
    ingested = [(job, ingest(job.trip_dir, trip_out_dir(out_dir, job) / "work")) for job in jobs]
    if any(not cache.has(img.sha256) for _, imgs in ingested for img in imgs) and not clients.vlm.healthy():
        raise RuntimeError("llama-server가 응답하지 않음. scripts/start_vlm.sh 로 기동하세요")
    works = [prepare_trip(job, imgs, out_dir, clients, cache, opts) for job, imgs in ingested]
    marked = mark_cross_trip_duplicates({str(w.job.trip_dir): w.receipts for w in works})
    for w in works:
        w.receipts = marked[str(w.job.trip_dir)]
    law = get_law_snapshot(clients.law, out_dir / ".cache", opts.today, refresh=opts.refresh_law)
    results: list[PipelineResult] = []
    for w in works:
        try:
            results.append(finalize_trip(w, law, clients, out_dir, opts, run_id))
        except Exception as e:  # 출장 하나의 실패가 일괄 처리 전체를 멈추지 않게 한다
            log.exception("finalize 실패 %s/%s", w.job.traveler, w.job.trip_id)
            results.append(_failed(w, law, run_id, e))
    md_path, json_path = write_summary(out_dir, run_id, results, warnings)
    return BatchResult(run_id=run_id, results=results, warnings=warnings, summary_md_path=str(md_path), summary_json_path=str(json_path))
```
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_pipeline.py` → `5 passed`; 전체 `uv run pytest` → 모두 passed
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/pipeline.py tests/helpers.py tests/test_pipeline.py && git commit -m "feat: 출장별 파이프라인과 여러 출장자·출장 일괄 처리, 요약"`

---

### Task 14: CLI (일괄 실행 · 필터 · 버전/법령/병렬 옵션)
**Files:** Create `src/receipt_evidence/cli.py`, `tests/test_cli.py`
**Interfaces:** Consumes `run_batch, Clients, RunOptions, LlamaServerClient, law_caller, kordoc_caller`. Produces `build_parser() -> argparse.ArgumentParser`, `main(argv: list[str] | None = None) -> int` (exit 0 정상, 2 실패·출장 오류, 3 합계 불일치)

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/test_cli.py
from receipt_evidence import cli
from receipt_evidence.mcp_client import FakeToolCaller
from receipt_evidence.models import BatchResult, PipelineResult, TripConfig

def _res(**kw):
    base = dict(run_id="b", traveler="정백철", trip_id="2026-07-09_서울", trip=TripConfig(traveler_name="정백철"), law_mst="287535",
                law_effective="2026-07-01", receipts=[], decisions=[], totals={"claimed": 196400, "approved": 196400, "review": 100000},
                review_items=["[stay] 숙박비: 지역 미확인"], report_md_path="r.md", hwpx_path="out/정백철/2026-07-09_서울/v1/evidence.hwpx",
                version=1, verify_ok=True)
    return PipelineResult(**(base | kw))

def _patch(monkeypatch, results, captured=None):
    def fake_batch(data_dir, out_dir, clients, *, travelers=None, trips=None, opts=None, run_id=None):
        if captured is not None:
            captured.update(travelers=travelers, trips=trips, workers=opts.workers, new_version=opts.new_version, refresh_law=opts.refresh_law)
        return BatchResult(run_id="b", results=results, warnings=["loose.jpg: data/<출장자>/<출장>/ 폴더에 넣어야 처리돼요"],
                           summary_md_path="out/summary-b.md", summary_json_path="out/summary-b.json")
    monkeypatch.setattr(cli, "run_batch", fake_batch)
    monkeypatch.setattr(cli, "law_caller", lambda: FakeToolCaller({}))
    monkeypatch.setattr(cli, "kordoc_caller", lambda: FakeToolCaller({}))

def test_parser_defaults():
    a = cli.build_parser().parse_args(["run"])
    assert (a.data, a.out, a.workers, a.traveler, a.trip, a.new_version, a.refresh_law, a.vlm_url) == (
        "data", "out", 1, None, None, False, False, "http://127.0.0.1:8088")

def test_run_prints_each_trip_and_passes_filters(monkeypatch, capsys):
    captured = {}
    _patch(monkeypatch, [_res()], captured)
    code = cli.main(["run", "--traveler", "정백철", "--trip", "2026-07-09_서울", "--workers", "2", "--new-version", "--refresh-law"])
    out = capsys.readouterr().out
    assert code == 0 and "정백철/2026-07-09_서울 v1 새 버전" in out and "확인필요 1건" in out and "loose.jpg" in out and "summary-b.md" in out
    assert captured == {"travelers": ["정백철"], "trips": ["2026-07-09_서울"], "workers": 2, "new_version": True, "refresh_law": True}

def test_exit_codes(monkeypatch):
    _patch(monkeypatch, [_res(verify_ok=False)])
    assert cli.main(["run"]) == 3
    _patch(monkeypatch, [_res(), _res(trip_id="2026-08-03_부산", error="RuntimeError: kordoc 실패", verify_ok=None, hwpx_path=None)])
    assert cli.main(["run"]) == 2

def test_check_vlm_unhealthy(monkeypatch):
    class Dead:
        def __init__(self, *a, **k): ...
        def healthy(self): return False
    monkeypatch.setattr(cli, "LlamaServerClient", Dead)
    assert cli.main(["check-vlm"]) == 2
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_cli.py` → `ModuleNotFoundError: No module named 'receipt_evidence.cli'`
- [ ] **Step 3: 최소 구현**
```python
# src/receipt_evidence/cli.py
from __future__ import annotations
import argparse, sys
from pathlib import Path
from .mcp_client import kordoc_caller, law_caller
from .pipeline import Clients, RunOptions, run_batch
from .vlm import LlamaServerClient

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="receipt-evidence", description="출장여비 영수증 증빙서류 HWPX 자동화")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="data/<출장자>/<출장>/ 전체를 일괄 처리")
    r.add_argument("--data", default="data", help="데이터 폴더 (기본 data)")
    r.add_argument("--out", default="out", help="출력 폴더 (기본 out)")
    r.add_argument("--traveler", action="append", default=None, help="이 출장자만 (여러 번 지정 가능)")
    r.add_argument("--trip", action="append", default=None, help="이 출장 폴더만 (여러 번 지정 가능)")
    r.add_argument("--workers", type=int, default=1, help="VLM 동시 처리 수 (llama-server VLM_PARALLEL과 맞출 것)")
    r.add_argument("--new-version", action="store_true", help="입력이 같아도 새 버전 문서를 만든다")
    r.add_argument("--refresh-law", action="store_true", help="오늘 법령 캐시를 무시하고 다시 조회")
    r.add_argument("--vlm-url", dest="vlm_url", default="http://127.0.0.1:8088")
    c = sub.add_parser("check-vlm", help="llama-server 상태 확인")
    c.add_argument("--vlm-url", dest="vlm_url", default="http://127.0.0.1:8088")
    return p

def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "check-vlm":
        ok = LlamaServerClient(a.vlm_url).healthy()
        print("llama-server OK" if ok else f"llama-server 응답 없음({a.vlm_url}). scripts/start_vlm.sh 실행 필요")
        return 0 if ok else 2
    opts = RunOptions(workers=a.workers, new_version=a.new_version, refresh_law=a.refresh_law)
    try:
        with law_caller() as law, kordoc_caller() as doc:
            res = run_batch(Path(a.data), Path(a.out), Clients(vlm=LlamaServerClient(a.vlm_url), law=law, doc=doc),
                            travelers=a.traveler, trips=a.trip, opts=opts)
    except Exception as e:  # 배치 전체가 시작조차 못 한 경우
        print(f"실패: {e}", file=sys.stderr)
        return 2
    for r in res.results:
        if r.error:
            print(f"{r.traveler}/{r.trip_id} 오류: {r.error}")
            continue
        state = "변경 없음" if r.skipped else "새 버전"
        print(f"{r.traveler}/{r.trip_id} v{r.version} {state} · 인정 {r.totals['approved']:,}원 · 확인필요 {len(r.review_items)}건 · {r.hwpx_path}")
        for item in r.review_items:
            print("    - " + item)
    for w in res.warnings:
        print("  ! " + w)
    print(f"요약: {res.summary_md_path}")
    if any(r.error for r in res.results):
        return 2
    return 3 if any(r.verify_ok is False for r in res.results) else 0

if __name__ == "__main__":
    sys.exit(main())
```
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_cli.py` → `4 passed`; `uv run receipt-evidence check-vlm` → 서버 미기동 시 `llama-server 응답 없음…` (exit 2)
- [ ] **Step 5: 커밋** — `git add src/receipt_evidence/cli.py tests/test_cli.py && git commit -m "feat: receipt-evidence 일괄 실행 CLI"`

---

### Task 15: Claude Code 스킬 + README
**Files:** Create `.claude/skills/receipt-evidence/SKILL.md`, `tests/test_skill_doc.py`; Modify `README.md`(uv init이 만든 파일 전체 교체)
**Interfaces:** Consumes CLI 명령·디렉터리 계약. Produces 스킬 문서(프론트매터 `name: receipt-evidence`)

- [ ] **Step 1: 실패하는 테스트 작성**
```python
# tests/test_skill_doc.py
from pathlib import Path

def test_skill_md_mentions_contract():
    md = Path(".claude/skills/receipt-evidence/SKILL.md").read_text(encoding="utf-8")
    assert md.startswith("---\nname: receipt-evidence")
    for needle in ("receipt-evidence run", "data/<출장자>/<출장>/", "traveler.yaml", "trip.yaml", "overrides.yaml", "start_vlm.sh", "summary-"):
        assert needle in md, needle

def test_readme_mentions_layout_and_tests():
    md = Path("README.md").read_text(encoding="utf-8")
    for needle in ("uv sync", "scripts/start_vlm.sh", "data/<출장자>/<출장>/", "uv run pytest -m integration"):
        assert needle in md, needle
```
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_skill_doc.py` → `FileNotFoundError: … SKILL.md`
- [ ] **Step 3: 최소 구현**
```markdown
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

## 절차
1. VLM 확인: `uv run receipt-evidence check-vlm`. exit 2면 `bash scripts/start_vlm.sh &`로 기동하고 `/health`가 ok가 될 때까지 기다린다(첫 로드 30~60초). 영수증이 많으면 `VLM_PARALLEL=4 bash scripts/start_vlm.sh &`와 `--workers 4`를 함께 쓴다.
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
```
`README.md` 전체를 아래로 교체:
````markdown
# receipt-evidence

출장여비 영수증을 로컬 AI(Qwen3-VL)로 읽고 공무원 여비 규정으로 판정해, 출장별 HWPX 증빙내역서를 만듭니다.

## 설치
```bash
uv sync
```
필요: llama.cpp(`llama-server`), Qwen3-VL GGUF(`scripts/start_vlm.sh` 경로), Node.js(`npx`로 kordoc·korean-law-mcp 실행).

## 영수증 넣기
```
data/<출장자>/traveler.yaml                 여비 구분·근무지·결재선 (examples/traveler.yaml)
data/<출장자>/<YYYY-MM-DD_출장지>/trip.yaml   출장기간·출장지·목적 (없으면 자동 제안)
data/<출장자>/<YYYY-MM-DD_출장지>/overrides.yaml  사용자 확인값 (선택)
data/<출장자>/<YYYY-MM-DD_출장지>/*.jpg|png|pdf  영수증
```
위 구조(`data/<출장자>/<출장>/`)로 넣어야 처리됩니다.

## 실행
```bash
bash scripts/start_vlm.sh &                 # 영수증이 많으면 VLM_PARALLEL=4 bash scripts/start_vlm.sh &
uv run receipt-evidence check-vlm
uv run receipt-evidence run                 # 전체 일괄
uv run receipt-evidence run --traveler 정백철 --trip 2026-07-09_서울 --workers 4
```
- 다시 실행하면 이미 읽은 영수증은 캐시를 쓰고, 입력이 바뀐 출장만 새 버전(`v2`, `v3` …)을 만듭니다.
- 결과: `out/<출장자>/<출장>/v<N>/evidence.hwpx`, 요약 `out/summary-<run_id>.md`.

## 테스트
```bash
uv run pytest                    # 단위 테스트 (네트워크·VLM 불필요)
uv run pytest -m integration     # 실제 llama-server·MCP 연동
```
````
- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_skill_doc.py` → `2 passed`
- [ ] **Step 5: 커밋** — `git add .claude/skills/receipt-evidence/SKILL.md README.md tests/test_skill_doc.py && git commit -m "docs: Claude Code 스킬과 README(폴더 계약·일괄·추가 제출)"`

---

### Task 16: 실데이터 End-to-End 통합 검증 (추가 제출 · 사용자 확인값)
**Files:** Create `tests/integration/test_e2e.py`
**Interfaces:** Consumes `run_batch, Clients, LlamaServerClient, law_caller, kordoc_caller`, 골든 `tests/fixtures/golden/receipts.json`

- [ ] **Step 1: 실패하는 테스트 작성** — 실제 영수증을 임시 폴더 구조로 복사해 1차(KTX 2장) → 2차(숙박 PDF 추가) → 3차(overrides.yaml) → 4차(변경 없음)를 한 함수에서 순차 검증한다.
```python
# tests/integration/test_e2e.py
import json, shutil
from pathlib import Path
import pytest
from receipt_evidence.mcp_client import kordoc_caller, law_caller
from receipt_evidence.models import Category, Verdict
from receipt_evidence.pipeline import Clients, run_batch
from receipt_evidence.vlm import LlamaServerClient

ROOT = Path(__file__).resolve().parents[2]
GOLD = {g["approval_no"]: g for g in json.loads((ROOT / "tests/fixtures/golden/receipts.json").read_text(encoding="utf-8"))}
SRC = {p.name: p for p in (ROOT / "data").rglob("*") if p.is_file()}  # 사용자가 data/를 폴더로 정리한 뒤에도 원본을 찾는다

class CountingVlm:
    def __init__(self, inner):
        self.inner, self.calls = inner, 0
    def healthy(self):
        return self.inner.healthy()
    def chat(self, messages, **kw):
        self.calls += 1
        return self.inner.chat(messages, **kw)

@pytest.mark.integration
def test_e2e_real_receipts_incremental(tmp_path):
    vlm = CountingVlm(LlamaServerClient())
    if not vlm.healthy():
        pytest.skip("llama-server 미기동 — bash scripts/start_vlm.sh & 후 재실행")
    data, out = tmp_path / "data", tmp_path / "out"
    trav = data / "정백철"; trip_dir = trav / "2026-07-09_서울"; trip_dir.mkdir(parents=True)
    (trav / "traveler.yaml").write_text("grade: 제2호\nworkplace_region: 나주\napproval: [담당, 팀장, 부장]\n", encoding="utf-8")
    (trip_dir / "trip.yaml").write_text("start_date: 2026-07-09\nend_date: 2026-07-10\ndestination_region: 서울\nroute_stations: [나주, 용산]\n", encoding="utf-8")
    for name in ("Screenshot_20260713_083024.jpg", "Screenshot_20260713_083037.jpg"):
        shutil.copy(SRC[name], trip_dir / name)

    with law_caller() as law, kordoc_caller() as doc:
        clients = Clients(vlm=vlm, law=law, doc=doc)

        r1 = run_batch(data, out, clients, run_id="e1").results[0]
        assert r1.error is None and r1.version == 1 and r1.verify_ok and len(r1.receipts) == 2 and r1.law_mst == "287535"
        for r in r1.receipts:
            g = GOLD[r.approval_no]
            assert r.category is Category(g["category"]) and r.amount == g["amount"] and str(r.service_date) == g["service_date"]
        assert r1.totals == {"claimed": 96400, "approved": 196400, "review": 0}

        shutil.copy(SRC["숙박 영수증.pdf"], trip_dir / "숙박 영수증.pdf")
        before = vlm.calls
        r2 = run_batch(data, out, clients, run_id="e2").results[0]
        assert r2.version == 2 and (r2.cache_hits, r2.cache_misses) == (2, 1) and vlm.calls - before >= 3
        assert r2.totals == {"claimed": 196400, "approved": 196400, "review": 100000} and r2.verify_ok
        stay = next(r for r in r2.receipts if r.category is Category.LODGING)
        assert next(d for d in r2.decisions if d.receipt_id == stay.receipt_id).verdict is Verdict.REVIEW
        assert "숙박비" in Path(r2.changes_md_path).read_text(encoding="utf-8")
        md = Path(r2.report_md_path).read_text(encoding="utf-8")
        assert "kca.kr" not in md and "53618190" not in md

        (trip_dir / "overrides.yaml").write_text(f"{stay.receipt_id}:\n  service_date: 2026-07-09\n  region: 서울\n", encoding="utf-8")
        before = vlm.calls
        r3 = run_batch(data, out, clients, run_id="e3").results[0]
        assert r3.version == 3 and vlm.calls == before and r3.totals["approved"] == 296400 and r3.verify_ok

        r4 = run_batch(data, out, clients, run_id="e4").results[0]
        assert r4.skipped and r4.version == 3
```
- [ ] **Step 2: 실패 확인** — VLM 미기동: `uv run pytest -m integration tests/integration/test_e2e.py` → `1 skipped`. `bash scripts/start_vlm.sh &` → `until curl -sf http://127.0.0.1:8088/health; do sleep 5; done` 후 재실행 → VLM 오인식이 있으면 `AssertionError`/`KeyError`(승인번호 오독)가 난다. `out/…/work/receipts.json`과 `transcripts/*.txt`로 틀린 필드를 확인한다.
- [ ] **Step 3: 최소 구현** — 오인식 필드가 있으면 `src/receipt_evidence/extract.py`의 `STRUCTURE_PROMPT` 끝(`[전사문]` 앞)에 해당 필드 지시를 한 문장 추가하고 `src/receipt_evidence/cache.py`의 `PROMPT_VERSION`을 `"p2"`로 올린다(이전 캐시 무효화). 예:
  - 승인번호를 승차권번호와 혼동: `"(9) approval_no는 '승인번호' 라벨 옆 숫자만. 승차권번호·주문번호와 혼동 금지."`
  - 운행일 대신 발행일시를 씀: `"(10) 철도 service_date는 '영수증' 제목 아래 운행 날짜이며 '발행일시'가 아님."`
  - 8B로도 계속 틀리면 `VLM_MODEL`/`VLM_MMPROJ` 환경변수로 4B Q8_0(같은 HF 캐시의 `models--Qwen--Qwen3-VL-4B-Instruct-GGUF/snapshots/1cd86afb9a95c410a6038ab3b40d8b578c892266/`)과 비교한다.
  - 수정 뒤 `uv run pytest tests/test_extract.py`가 여전히 `10 passed`인지 확인한다(캐시 경로 테스트가 `p1`을 기대하면 `PROMPT_VERSION` 값에 맞춰 테스트 기대 경로도 함께 수정).
- [ ] **Step 4: 통과 확인** — `uv run pytest -m integration tests/integration/test_e2e.py` → `1 passed`; `uv run pytest` → 전체 passed(integration 제외)
- [ ] **Step 5: 실제 데이터로 실행 (사용자 확인 후)** — 사용자에게 `data/`의 3개 파일을 `data/정백철/2026-07-09_서울/`로 옮겨도 되는지, 그리고 `traveler.yaml`·`trip.yaml` 값(여비 구분·근무지·결재선·출장 목적)을 묻는다. 승인을 받은 뒤에만 옮기고 설정 파일을 만든 다음 `uv run receipt-evidence run`을 실행한다. 출력의 버전·인정액·확인필요와 `out/summary-<run_id>.md`를 보고한다.
- [ ] **Step 6: 커밋** — `git add tests/integration/test_e2e.py src/receipt_evidence/extract.py src/receipt_evidence/cache.py && git commit -m "test: 실데이터 E2E(추가 제출·사용자 확인값·변경 없음)"`

---

## 자체 점검
- 요구사항 1(VLM 스캔→JSON): Task 2·3·4·5. 요구사항 2(korean-law 규정 검색): Task 6·7. 요구사항 3(표 정리·지급대상): Task 9·10. 요구사항 4(kordoc 증빙서식 HWPX): Task 11·13·16.
- 확장성 결정: 한 출장 다건(Task 4 캐시·병렬, 9 교차검사, 10 정렬, 11 붙임 축소), 여러 출장 한꺼번에(Task 8 발견, 6 세션 재사용, 7 법령 일자 캐시, 13 실패 격리), 여러 출장자(Task 8 traveler.yaml, 13 요약, 14 필터), 추가 제출(Task 4 캐시, 12 버전, 13 changes.md, 16 E2E), 출장 정보 자동 제안(Task 8, 9 정액 행 확인필요, 10 표기).
- 시그니처 일관성: `Receipt/TravelerProfile/TripConfig/PipelineResult/BatchResult`(1) → `ingest`(2) → `LlamaServerClient/FakeVlmClient`(3) → `ExtractCache/extract_receipts(cache, workers)`(4) → `validate_all`(5) → `StdioToolCaller/FakeToolCaller`(6) → `fetch_law_snapshot/get_law_snapshot/html_table_rows`(7) → `discover/TripJob/load_traveler/resolve_trip/apply_overrides/dump_trip_yaml`(8) → `apply_cross_checks/mark_cross_trip_duplicates/decide_all/totals/review_items/RULES_VERSION`(9) → `build_markdown(version)/fmt_won/md_table/DETAIL_HEADERS`(10) → `prepare_attachments/export_hwpx/verify_hwpx`(11) → `fingerprint/plan_version/write_latest/load_decisions/diff_markdown`(12) → `run_batch/Clients/RunOptions`(13) → `cli.main`(14).
- Fake 주입: `FakeVlmClient`, `ColorVlm`, `FakeToolCaller`, 오프라인 echo MCP 서버로 단위테스트는 네트워크·VLM·npx 없이 실행. 실제 연동은 `integration` 마커.
