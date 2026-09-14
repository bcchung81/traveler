"""웹앱이 data/·out/ 폴더 계약을 읽고 쓰는 계층. 경로 성분은 모두 검증해 data/·out/ 밖으로 나가지 못하게 한다."""
from __future__ import annotations
import hashlib, json, re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import yaml
from ..extract import parse_datetime
from ..ingest import SUPPORTED, sha256_file
from ..law import LawBook, load_law_book, peek_law_book
from ..models import Category, PipelineResult, Receipt, ReceiptImage, TravelerProfile
from ..rules import MANUAL_VERDICTS
from ..versioning import read_latest
from ..workspace import (STAGING_PREFIX, TRIP_DIR_RE, TRIP_YAML_FIELDS, HashCache, NotFound, Suggestion, apply_overrides, is_staging,
                         load_overrides, load_traveler, move_trip, nfc, resolve_moved, staging_trip_id, suggest_trip, trip_file_owners,
                         trip_confirmed, unique_trip_id, was_auto_named)

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
PROFILE_FIELDS = ("position", "grade", "org", "dept", "workplace_region", "approval")
GRADES = ("제1호", "제2호")
ALLOWANCE_SLUGS = {"daily": "일비", "meal": "식비", "incity": "근무지 내 출장 여비"}  # 주소·화면 id용(한글·공백 없이)
DEFAULT_GRADE = "제2호"  # 새 출장자 기본값(일반 공무원·직원). 확인 카드에서 바꿀 수 있다
CONFIRM_TRIP_FIELDS = ("start_date", "end_date", "destination_region", "route_stations", "lodging_region")
IMAGE_ID_RE = re.compile(r"^[0-9a-f]{12}-p\d+$")
WARNING_CODE_RE = re.compile(r"^[A-Z_]{3,40}$")
VERSION_FILES = ("evidence.hwpx", "preview.html", "changes.md", "report.md")
RECEIPT_TEXT_FIELDS = ("merchant", "business_no", "origin", "destination", "seat_class", "train_no", "approval_no", "region", "payer_name")
RECEIPT_DATE_FIELDS = ("service_date", "service_end_date")
RECEIPT_INT_FIELDS = ("amount", "nights")
EDITABLE_RECEIPT_FIELDS = (("category", "paid_at") + RECEIPT_TEXT_FIELDS + RECEIPT_DATE_FIELDS + RECEIPT_INT_FIELDS)

class InvalidName(ValueError):
    """경로 성분(출장자·출장·파일명·이미지 id·버전 파일)이 규칙에 맞지 않음."""

class TripMoved(Exception):
    """출장 폴더 이름이 바뀌어 옛 주소로 들어옴 — 새 주소로 보낸다."""
    def __init__(self, traveler: str, old: str, new: str):
        super().__init__(f"{traveler}/{old} → {new}")
        self.traveler, self.old, self.new = traveler, old, new

@dataclass(frozen=True)
class FileInfo:
    name: str
    size: int
    kind: str

@dataclass(frozen=True)
class VersionInfo:
    version: int
    hwpx: Path
    report_md: Path
    preview_html: Path | None
    changes_md: Path | None
    approved: int
    review_count: int
    verify_ok: bool | None

@dataclass(frozen=True)
class TripSummary:
    traveler: str
    trip_id: str
    start_date: date | None
    end_date: date | None
    destination: str
    files: int
    extracted: bool
    stale: bool
    version: int | None
    claimed: int | None
    approved: int | None
    review_count: int | None
    verify_ok: bool | None
    proposed: bool
    stage: str
    staging: bool = False
    display_name: str = ""

def _name(value: object) -> str:
    s = nfc(str(value)).strip()
    if not s or len(s) > 60 or any(c in s for c in "/\\\x00") or s.startswith("."):
        raise InvalidName(f"사용할 수 없는 이름: {value!r}")
    return s

def _blank(v: object) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())

def _yaml_load(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

def _yaml_dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

def _is_receipt_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith(".")

def _trip_value(key: str, v: object) -> object:
    if key in ("start_date", "end_date"):
        if _blank(v):
            return None
        return v if isinstance(v, date) else date.fromisoformat(str(v).strip())
    if key == "route_stations":
        if isinstance(v, list):
            return [nfc(str(x)).strip() for x in v if str(x).strip()]
        return [nfc(x).strip() for x in str(v or "").split(",") if x.strip()]
    if key in ("official_vehicle", "within_workplace"):
        return v if isinstance(v, bool) else str(v).strip().lower() in ("on", "true", "1", "yes")
    if key == "duration_hours":
        return None if _blank(v) else float(str(v).strip())
    return None if _blank(v) else nfc(str(v)).strip()

def _receipt_value(key: str, v: object) -> object:
    if _blank(v):
        return None
    s = nfc(str(v)).strip()
    if key in RECEIPT_INT_FIELDS:
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else None
    if key in RECEIPT_DATE_FIELDS:
        return date.fromisoformat(s)
    if key == "paid_at":
        return parse_datetime(s)
    if key == "category":
        return Category(s).value
    return s

class TripService:
    def __init__(self, data_dir: Path, out_dir: Path):
        self.data_dir = Path(data_dir)
        self.out_dir = Path(out_dir)
        self.hashes = HashCache(self.out_dir / ".cache" / "hashes.json")  # 홈 화면이 열릴 때마다 모든 파일을 다시 해시하지 않게

    # ---- 경로 ----
    @staticmethod
    def _inside(base: Path, path: Path) -> Path:
        root, target = base.resolve(), path.resolve()
        if target != root and root not in target.parents:
            raise InvalidName(f"허용되지 않은 경로: {path}")
        return path

    def traveler_dir(self, traveler: str) -> Path:
        return self._inside(self.data_dir, self.data_dir / _name(traveler))

    def trip_dir(self, traveler: str, trip_id: str) -> Path:
        return self._inside(self.data_dir, self.data_dir / _name(traveler) / _name(trip_id))

    def trip_out(self, traveler: str, trip_id: str) -> Path:
        return self._inside(self.out_dir, self.out_dir / _name(traveler) / _name(trip_id))

    # ---- 출장·파일 ----
    def create_trip(self, traveler: str, start_date: date, destination: str) -> tuple[str, str]:
        t = _name(traveler)
        trip_id = _name(f"{start_date.isoformat()}_{_name(destination).replace('_', ' ')}")
        self.trip_dir(t, trip_id).mkdir(parents=True, exist_ok=True)
        return t, trip_id

    @staticmethod
    def check_uploads(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
        """전부 검증한 뒤에 저장한다(일부만 저장되거나 빈 출장 폴더가 남는 일 방지)."""
        prepared = []
        for raw_name, content in files:
            name = _name(Path(nfc(raw_name).replace("\\", "/")).name)
            if Path(name).suffix.lower() not in SUPPORTED:
                raise ValueError(f"{name}: 지원하지 않는 확장자예요(jpg·png·pdf·heic)")
            if len(content) > MAX_UPLOAD_BYTES:
                raise ValueError(f"{name}: 파일이 30MB를 넘어요")
            prepared.append((name, content))
        return prepared

    def travelers(self) -> list[str]:
        if not self.data_dir.exists():
            return []
        names = []
        for p in sorted(self.data_dir.iterdir(), key=lambda p: nfc(p.name)):
            try:
                if p.is_dir():
                    names.append(_name(p.name))
            except InvalidName:
                continue
        return names

    def ensure_traveler(self, traveler: str) -> str:
        """출장자 폴더를 만들고, 여비 구분이 정해지지 않았으면 제2호를 기본으로 적어 둔다."""
        t = _name(traveler)
        path = self.traveler_dir(t) / "traveler.yaml"
        data = _yaml_load(path)
        if not data.get("grade"):
            data["grade"] = DEFAULT_GRADE
            _yaml_dump(path, data)
        return t

    def create_staging_trip(self, traveler: str, files: list[tuple[str, bytes]]) -> tuple[str, str]:
        """첫 화면: 출장 정보 없이 영수증만 받아 임시 출장 폴더에 저장한다."""
        prepared = self.check_uploads(files)
        if not prepared:
            raise ValueError("영수증 파일을 올려 주세요")
        t = self.ensure_traveler(traveler)
        trip_id = unique_trip_id(self.data_dir, t, staging_trip_id())
        self.trip_dir(t, trip_id).mkdir(parents=True)
        self.save_files(t, trip_id, prepared)
        return t, trip_id

    def trip_suggestion(self, traveler: str, trip_id: str) -> dict[str, Suggestion]:
        t, trip = _name(traveler), _name(trip_id)
        # 자동으로 붙인 폴더 이름은 사용자가 정한 게 아니므로 근거로 쓰지 않고 영수증에서 다시 제안한다
        named_by_user = not (is_staging(trip) or was_auto_named(self.out_dir, t, trip))
        return suggest_trip(trip if named_by_user else STAGING_PREFIX, self.receipts(t, trip), self.load_profile(t).workplace_region)

    def moved_to(self, traveler: str, trip_id: str) -> str | None:
        if self.trip_dir(traveler, trip_id).is_dir():
            return None
        return resolve_moved(self.out_dir, _name(traveler), _name(trip_id))

    def auto_rename(self, traveler: str, trip_id: str, start: date | None, destination: str | None) -> str:
        """임시 폴더나 자동으로 이름 붙인 폴더(서류 전)만 YYYY-MM-DD_출장지로 바꾼다. 최종 이름을 돌려준다."""
        t, trip = _name(traveler), _name(trip_id)
        if not start or _blank(destination):
            return trip
        if not (is_staging(trip) or was_auto_named(self.out_dir, t, trip)) or (self.trip_out(t, trip) / "latest.json").exists():
            return trip
        base_id = _name(f"{start.isoformat()}_{_name(destination).replace('_', ' ')}")
        if re.fullmatch(re.escape(base_id) + r"(_\d+)?", trip):
            return trip
        return move_trip(self.data_dir, self.out_dir, t, trip, unique_trip_id(self.data_dir, t, base_id))

    def confirm_trip(self, traveler: str, trip_id: str, fields: dict) -> str:
        """확인 카드: 출장 정보는 trip.yaml, 근무지·여비 구분은 traveler.yaml. 폴더 이름도 확정값에 맞춘다."""
        t, trip = _name(traveler), _name(trip_id)
        self.save_profile(t, {k: fields[k] for k in ("grade", "workplace_region") if k in fields})
        self.save_trip_yaml(t, trip, {k: fields[k] for k in CONFIRM_TRIP_FIELDS if k in fields})
        y = self.load_trip_yaml(t, trip)
        return self.auto_rename(t, trip, y.get("start_date"), y.get("destination_region"))

    def save_files(self, traveler: str, trip_id: str, files: list[tuple[str, bytes]]) -> list[str]:
        d = self.trip_dir(traveler, trip_id)
        prepared = self.check_uploads(files)
        d.mkdir(parents=True, exist_ok=True)
        saved = []
        for name, content in prepared:
            stem, suffix = Path(name).stem, Path(name).suffix
            digest, n, target = hashlib.sha256(content).hexdigest(), 1, d / name
            while target.exists() and sha256_file(target) != digest:
                n += 1
                target = d / f"{stem}-{n}{suffix}"
            target.write_bytes(content)
            saved.append(target.name)
        return saved

    def list_files(self, traveler: str, trip_id: str) -> list[FileInfo]:
        d = self.trip_dir(traveler, trip_id)
        if not d.exists():
            return []
        files = sorted((p for p in d.iterdir() if _is_receipt_file(p)), key=lambda p: nfc(p.name))
        return [FileInfo(nfc(p.name), p.stat().st_size, "PDF" if p.suffix.lower() == ".pdf" else "사진") for p in files]

    def delete_file(self, traveler: str, trip_id: str, name: str) -> None:
        target = _name(name)
        d = self.trip_dir(traveler, trip_id)
        for p in d.iterdir() if d.exists() else []:
            if _is_receipt_file(p) and nfc(p.name) == target:
                p.unlink()
                return
        raise FileNotFoundError(name)

    # ---- 설정 ----
    def load_profile(self, traveler: str) -> TravelerProfile:
        return load_traveler(self.traveler_dir(traveler))

    def save_profile(self, traveler: str, fields: dict) -> None:
        path = self.traveler_dir(traveler) / "traveler.yaml"
        data = _yaml_load(path)
        for key in PROFILE_FIELDS:
            if key not in fields:
                continue
            v = fields[key]
            if key == "approval":
                items = [nfc(x).strip() for x in v.split(",")] if isinstance(v, str) else [nfc(str(x)).strip() for x in (v or [])]
                items = [x for x in items if x]
                if items:
                    data[key] = items
                else:
                    data.pop(key, None)
            elif _blank(v):
                data.pop(key, None)
            elif key == "grade" and str(v).strip() not in GRADES:
                raise ValueError(f"여비 구분은 {GRADES} 중 하나여야 해요")
            else:
                data[key] = nfc(str(v)).strip()
        _yaml_dump(path, data)

    def load_trip_yaml(self, traveler: str, trip_id: str) -> dict:
        return _yaml_load(self.trip_dir(traveler, trip_id) / "trip.yaml")

    def save_trip_yaml(self, traveler: str, trip_id: str, fields: dict) -> None:
        path = self.trip_dir(traveler, trip_id) / "trip.yaml"
        data = _yaml_load(path)
        for key in TRIP_YAML_FIELDS:
            if key in fields:
                data[key] = _trip_value(key, fields[key])
        _yaml_dump(path, data)

    # ---- 영수증·사용자 확인값 ----
    def extracted(self, traveler: str, trip_id: str) -> list[Receipt]:
        p = self.trip_out(traveler, trip_id) / "work" / "receipts.extracted.json"
        if not p.exists():
            return []
        return [Receipt.model_validate(d) for d in json.loads(p.read_text(encoding="utf-8"))]

    def receipts(self, traveler: str, trip_id: str) -> list[Receipt]:
        return apply_overrides(self.extracted(traveler, trip_id), load_overrides(self.trip_dir(traveler, trip_id)))

    def save_override(self, traveler: str, trip_id: str, receipt_id: str, fields: dict, clear_warnings: list[str] | None = None) -> None:
        """VLM 추출값과 다른 필드만 overrides.yaml에 남긴다. clear_warnings=None이면 기존 해제 목록을 건드리지 않는다."""
        base = {r.receipt_id: r for r in self.extracted(traveler, trip_id)}
        if receipt_id not in base:
            raise NotFound(f"영수증 {receipt_id}")
        orig = base[receipt_id]
        path = self.trip_dir(traveler, trip_id) / "overrides.yaml"
        data = _yaml_load(path)
        entry = dict(data.get(receipt_id) or {})
        for key, value in fields.items():
            if key not in EDITABLE_RECEIPT_FIELDS:
                continue
            new = _receipt_value(key, value)
            old = orig.category.value if key == "category" else getattr(orig, key)
            if new == old:
                entry.pop(key, None)
            else:
                entry[key] = new
        if clear_warnings is not None:
            codes = sorted({c for c in clear_warnings if WARNING_CODE_RE.match(c)})
            if codes:
                entry["clear_warnings"] = codes
            else:
                entry.pop("clear_warnings", None)
        self._write_override(path, data, receipt_id, entry)

    @staticmethod
    def _write_override(path: Path, data: dict, receipt_id: str, entry: dict) -> None:
        if entry:
            data[receipt_id] = entry
        else:
            data.pop(receipt_id, None)
        if data:
            _yaml_dump(path, data)
        elif path.exists():
            path.unlink()

    def save_allowance_decision(self, traveler: str, trip_id: str, slug: str, mode: str, days: object = "", amount: object = "",
                                reason: str = "") -> None:
        """담당자 정액 판정(trip.yaml allowance_decisions): 규정대로(삭제)·일수·금액·불인정. 사유 필수, 근무지 내 출장 여비는 금액만."""
        item = ALLOWANCE_SLUGS.get(slug)
        if item is None:
            raise NotFound(f"정액 항목 {slug}")
        path = self.trip_dir(traveler, trip_id) / "trip.yaml"
        data = _yaml_load(path)
        decisions = dict(data.get("allowance_decisions") or {})
        mode = nfc(str(mode or "")).strip()
        if mode in ("", "규정대로"):
            decisions.pop(item, None)
        else:
            text = nfc(str(reason or "")).strip()
            if mode not in ("일수", "금액", "불인정"):
                raise ValueError("판정은 규정대로·인정 일수·금액 지정·불인정 중 하나여야 해요")
            if not text:
                raise ValueError("판정을 바꾼 사유를 적어 주세요")
            if mode == "일수":
                if item == "근무지 내 출장 여비":
                    raise ValueError("근무지 내 출장 여비는 금액으로만 고칠 수 있어요")
                raw = str(days or "").strip()
                if not re.fullmatch(r"\d{1,3}", raw):
                    raise ValueError("인정 일수는 0 이상의 정수로 적어 주세요")
                decisions[item] = {"days": int(raw), "reason": text}
            elif mode == "금액":
                digits = re.sub(r"[^\d]", "", str(amount or ""))
                if not digits:
                    raise ValueError("인정 금액을 적어 주세요")
                decisions[item] = {"amount": int(digits), "reason": text}
            else:
                decisions[item] = {"amount": 0, "reason": text}
        if decisions:
            data["allowance_decisions"] = decisions
        else:
            data.pop("allowance_decisions", None)
        if data:
            _yaml_dump(path, data)
        elif path.exists():
            path.unlink()

    def save_decision(self, traveler: str, trip_id: str, receipt_id: str, verdict: str, amount: object = "", reason: str = "") -> None:
        """담당자 판정: 규정대로(삭제)·지급·감액지급·불인정. 사유는 필수, 감액은 0<금액<청구액. 다른 확인값은 건드리지 않는다."""
        receipts = {r.receipt_id: r for r in self.receipts(traveler, trip_id)}
        if receipt_id not in receipts:
            raise NotFound(f"영수증 {receipt_id}")
        path = self.trip_dir(traveler, trip_id) / "overrides.yaml"
        data = _yaml_load(path)
        entry = dict(data.get(receipt_id) or {})
        verdict = nfc(str(verdict or "")).strip()
        if verdict in ("", "규정대로"):
            entry.pop("decision", None)
        else:
            if verdict not in MANUAL_VERDICTS:
                raise ValueError("판정은 규정대로·지급·감액지급·불인정 중 하나여야 해요")
            text = nfc(str(reason or "")).strip()
            if not text:
                raise ValueError("판정을 바꾼 사유를 적어 주세요")
            decision: dict = {"verdict": verdict, "reason": text}
            if verdict == "감액지급":
                claimed = receipts[receipt_id].amount or 0
                digits = re.sub(r"[^\d]", "", str(amount or ""))
                value = int(digits) if digits else 0
                if not 0 < value < claimed:
                    raise ValueError(f"감액 인정액은 0보다 크고 청구액 {claimed:,}원보다 작아야 해요")
                decision["approved_amount"] = value
            entry["decision"] = decision
        self._write_override(path, data, receipt_id, entry)

    def _manifest(self, traveler: str, trip_id: str) -> list[ReceiptImage]:
        p = self.trip_out(traveler, trip_id) / "work" / "manifest.json"
        if not p.exists():
            return []
        return [ReceiptImage.model_validate(d) for d in json.loads(p.read_text(encoding="utf-8"))]

    def images_for(self, traveler: str, trip_id: str) -> dict[str, list[str]]:
        images = self._manifest(traveler, trip_id)
        pages: dict[str, list[ReceiptImage]] = {}
        for img in images:
            pages.setdefault(img.sha256, []).append(img)
        sha_of = {img.image_id: img.sha256 for img in images}
        return {r.receipt_id: [i.image_id for i in sorted(pages.get(r.sha256 or sha_of.get(r.image_id, ""), []), key=lambda i: i.page)]
                for r in self.extracted(traveler, trip_id)}

    def image_path(self, traveler: str, trip_id: str, image_id: str) -> Path:
        if not IMAGE_ID_RE.match(image_id):
            raise InvalidName(f"잘못된 이미지 id: {image_id!r}")
        p = self.trip_out(traveler, trip_id) / "work" / "images" / f"{image_id}.png"
        if not p.exists():
            raise FileNotFoundError(image_id)
        return p

    def law_book(self, today: date) -> LawBook | None:
        """오늘 조회했거나(실패 시 저장해 둔 규정) 방금 조회에 실패한 기록이 있으면 규정 묶음, 조회가 필요하면 None."""
        return load_law_book(self.out_dir / ".cache", today)

    def law_notices(self, today: date) -> list[str]:
        book = peek_law_book(self.out_dir / ".cache")
        return book.notices(today) if book else []

    def file_owners(self) -> dict[str, set[str]]:
        return trip_file_owners(self.data_dir, self.hashes)

    # ---- 상태·버전 ----
    def trip_status(self, traveler: str, trip_id: str) -> TripSummary:
        t, trip_id = _name(traveler), _name(trip_id)
        d, out = self.trip_dir(t, trip_id), self.trip_out(t, trip_id)
        files = [p for p in d.iterdir() if _is_receipt_file(p)] if d.exists() else []
        trip_yaml = _yaml_load(d / "trip.yaml")
        extracted = (out / "work" / "receipts.extracted.json").exists()
        stale = False
        manifest = out / "work" / "manifest.json"
        if extracted and manifest.exists():
            known = {m["sha256"] for m in json.loads(manifest.read_text(encoding="utf-8"))}
            stale = {self.hashes.sha256(p) for p in files} != known
        version = claimed = approved = review_count = verify_ok = None
        documented = False
        latest = read_latest(out) if out.exists() else None
        if latest:
            version = int(latest["version"])
            result_path = out / f"v{version}" / "result.json"
            if result_path.exists():
                res = json.loads(result_path.read_text(encoding="utf-8"))
                claimed, approved = res["totals"].get("claimed"), res["totals"].get("approved")
                review_count, verify_ok = len(res.get("review_items", [])), res.get("verify_ok")
                # 사람이 고치는 입력(설정·확인값)이 문서보다 새로우면 문서가 최신이 아니다. 파일 변경은 stale로 따로 본다.
                inputs = [p for p in (self.traveler_dir(t) / "traveler.yaml", d / "trip.yaml", d / "overrides.yaml") if p.exists()]
                documented = all(result_path.stat().st_mtime >= p.stat().st_mtime for p in inputs)
        m = TRIP_DIR_RE.match(trip_id)
        folder_date = None
        if m:
            try:
                folder_date = date.fromisoformat(m.group(1))
            except ValueError:
                folder_date = None
        if not files:
            stage = "empty"
        elif not extracted or stale:
            stage = "uploaded"
        elif documented:
            stage = "documented"
        else:
            stage = "extracted"
        staging = trip_id.startswith(STAGING_PREFIX)
        display = trip_id
        if staging:
            sm = re.match(rf"{re.escape(STAGING_PREFIX)}\d{{4}}(\d{{2}})(\d{{2}})-(\d{{2}})(\d{{2}})", trip_id)
            display = f"새 정산 ({int(sm.group(1))}. {int(sm.group(2))}. {sm.group(3)}:{sm.group(4)})" if sm else "새 정산"
        return TripSummary(traveler=t, trip_id=trip_id, start_date=trip_yaml.get("start_date") or folder_date, end_date=trip_yaml.get("end_date"),
                           destination=trip_yaml.get("destination_region") or (m.group(2) if m else ""), files=len(files),
                           extracted=extracted, stale=stale, version=version, claimed=claimed, approved=approved,
                           review_count=review_count, verify_ok=verify_ok, proposed=not trip_confirmed(d), stage=stage,
                           staging=staging, display_name=display)

    def list_trips(self) -> list[TripSummary]:
        if not self.data_dir.exists():
            return []
        found = []
        for tdir in sorted((p for p in self.data_dir.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: nfc(p.name)):
            try:
                traveler = _name(tdir.name)
            except InvalidName:
                continue
            for trip in sorted((p for p in tdir.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: nfc(p.name)):
                try:
                    found.append(self.trip_status(traveler, _name(trip.name)))
                except InvalidName:
                    continue
        self.hashes.flush()
        return found

    def versions(self, traveler: str, trip_id: str) -> list[VersionInfo]:
        out = self.trip_out(traveler, trip_id)
        if not out.exists():
            return []
        items = []
        for v in sorted((p for p in out.iterdir() if p.is_dir() and re.fullmatch(r"v\d+", p.name)), key=lambda p: int(p.name[1:])):
            result_path = v / "result.json"
            if not result_path.exists():
                continue
            res = json.loads(result_path.read_text(encoding="utf-8"))
            items.append(VersionInfo(version=int(v.name[1:]), hwpx=v / "evidence.hwpx", report_md=v / "report.md",
                                     preview_html=(v / "preview.html") if (v / "preview.html").exists() else None,
                                     changes_md=(v / "changes.md") if (v / "changes.md").exists() else None,
                                     approved=res["totals"].get("approved", 0), review_count=len(res.get("review_items", [])),
                                     verify_ok=res.get("verify_ok")))
        return items

    def latest_result(self, traveler: str, trip_id: str) -> PipelineResult | None:
        out = self.trip_out(traveler, trip_id)
        latest = read_latest(out) if out.exists() else None
        if not latest:
            return None
        p = out / f"v{int(latest['version'])}" / "result.json"
        return PipelineResult.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None

    def version_file(self, traveler: str, trip_id: str, version: int, name: str) -> Path:
        if name not in VERSION_FILES:
            raise InvalidName(f"허용되지 않은 문서 파일: {name!r}")
        p = self.trip_out(traveler, trip_id) / f"v{int(version)}" / name
        if not p.exists():
            raise FileNotFoundError(f"v{version}/{name}")
        return p
