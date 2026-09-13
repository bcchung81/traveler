# src/receipt_evidence/workspace.py
from __future__ import annotations
import fcntl, json, os, re, threading, unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import yaml
from .ingest import SUPPORTED, sha256_file
from .models import Category, Receipt, TravelerProfile, TripConfig
from .validate import FIELD_GROUP, mark_dup_approval, validate_receipt

TRIP_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_([^_]+)")
TRIP_YAML_FIELDS = ("start_date", "end_date", "destination_region", "purpose", "route_stations", "lodging_region",
                    "over_cap_reason", "taxi_reason", "official_vehicle", "within_workplace", "duration_hours")

class NotFound(LookupError):
    """사용자가 가리킨 출장·영수증이 없음(웹에서 404). 코드 버그로 난 KeyError와 구분하려고 따로 둔다."""

class BusyError(RuntimeError):
    """같은 out/ 폴더에서 다른 정산 작업(CLI 또는 웹)이 이미 돌고 있음."""

@contextmanager
def out_lock(out_dir: Path) -> Iterator[None]:
    """out/ 쓰기 작업을 프로세스 사이에서 하나만 돌게 한다. 기다리지 않고 바로 알린다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    f = open(out_dir / ".lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise BusyError("다른 정산 작업(CLI 또는 웹)이 실행 중이에요. 끝난 뒤 다시 실행해 주세요") from None
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()

class HashCache:
    """파일 경로 → (크기, 수정시각, sha256). 크기·수정시각이 같으면 다시 읽지 않는다(홈 화면·출장 간 중복 검사용)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, list] | None = None
        self._dirty = False

    def _entries(self) -> dict[str, list]:
        if self._data is None:
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                self._data = {}
        return self._data

    def sha256(self, path: Path) -> str:
        st = path.stat()
        key, sig = str(path.resolve()), [st.st_size, st.st_mtime_ns]
        with self._lock:
            hit = self._entries().get(key)
            if hit and hit[:2] == sig:
                return hit[2]
        digest = sha256_file(path)
        with self._lock:
            self._entries()[key] = sig + [digest]
            self._dirty = True
        return digest

    def flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            data = {k: v for k, v in self._entries().items() if os.path.exists(k)}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
            self._data, self._dirty = data, False

@dataclass(frozen=True)
class TripJob:
    traveler: str
    trip_id: str
    traveler_dir: Path
    trip_dir: Path

def nfc(s: str) -> str:
    """macOS Finder가 만든 한글 파일·폴더 이름(NFD)을 입력 문자열과 같은 NFC로 맞춘다."""
    return unicodedata.normalize("NFC", s)

def _is_receipt(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith(".")

def _subdirs(p: Path) -> list[Path]:
    return sorted(d for d in p.iterdir() if d.is_dir() and not d.name.startswith("."))

def discover(data_dir: Path, travelers: list[str] | None = None, trips: list[str] | None = None) -> tuple[list[TripJob], list[str]]:
    jobs: list[TripJob] = []
    want_travelers = {nfc(t) for t in travelers} if travelers else None
    want_trips = {nfc(t) for t in trips} if trips else None
    warnings = [f"{nfc(p.name)}: data/<출장자>/<출장>/ 폴더에 넣어야 처리돼요" for p in sorted(data_dir.iterdir()) if _is_receipt(p)]
    for tdir in _subdirs(data_dir):
        traveler = nfc(tdir.name)
        if want_travelers and traveler not in want_travelers:
            continue
        loose = sorted(nfc(p.name) for p in tdir.iterdir() if _is_receipt(p))
        if loose:
            warnings.append(f"{traveler}/{', '.join(loose)}: 출장 폴더에 넣어야 처리돼요")
        for trip in _subdirs(tdir):
            trip_id = nfc(trip.name)
            if want_trips and trip_id not in want_trips:
                continue
            if not any(_is_receipt(p) for p in trip.iterdir()):
                warnings.append(f"{traveler}/{trip_id}: 영수증이 없어 건너뜀")
                continue
            jobs.append(TripJob(traveler=traveler, trip_id=trip_id, traveler_dir=tdir, trip_dir=trip))
    return jobs, warnings

def trip_key(traveler: str, trip_id: str) -> str:
    return f"{traveler}/{trip_id}"

def trip_file_owners(data_dir: Path, hashes: HashCache) -> dict[str, set[str]]:
    """data/ 전체 출장 폴더의 영수증 파일 해시 → 그 파일이 있는 출장들. 웹(출장 1건)과 CLI(일괄)가 같은 기준으로 중복을 본다."""
    owners: dict[str, set[str]] = {}
    if not data_dir.exists():
        return owners
    for job in discover(data_dir)[0]:
        for p in job.trip_dir.iterdir():
            if _is_receipt(p):
                owners.setdefault(hashes.sha256(p), set()).add(trip_key(job.traveler, job.trip_id))
    hashes.flush()
    return owners

def _yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

def load_traveler(traveler_dir: Path) -> TravelerProfile:
    data = _yaml(traveler_dir / "traveler.yaml")
    data.setdefault("name", nfc(traveler_dir.name))
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
    m = TRIP_DIR_RE.match(nfc(trip_id))
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

def _transcript(r: Receipt) -> str:
    try:
        return Path(r.transcript_path).read_text(encoding="utf-8") if r.transcript_path else ""
    except OSError:
        return ""

def clear_warnings(receipts: list[Receipt], overrides: dict[str, dict]) -> list[Receipt]:
    """사용자가 '확인함'으로 해제한 경고를 지운다. 교차검사가 경고를 더한 뒤에도 다시 불러 마지막에 적용한다."""
    out = []
    for r in receipts:
        cleared = set((overrides.get(r.receipt_id) or {}).get("clear_warnings", []))
        kept = [w for w in r.warnings if w not in cleared]
        out.append(r if kept == r.warnings else r.model_copy(update={"warnings": kept}))
    return out

def apply_overrides(receipts: list[Receipt], overrides: dict[str, dict]) -> list[Receipt]:
    """사용자 확인값을 합친다. 고친 필드는 영수증 원문과 다시 대조하고, 해제한 경고는 마지막에 지운다."""
    out: list[Receipt] = []
    changed = False
    for r in receipts:
        o = overrides.get(r.receipt_id)
        if not o:
            out.append(r)
            continue
        fields = {k: v for k, v in o.items() if k not in ("warnings", "clear_warnings", "receipt_id", "image_id", "sha256")}
        merged = r.model_dump() | fields
        merged["raw"] = r.raw | {"overrides": o}
        new = Receipt.model_validate(merged)
        groups = {FIELD_GROUP[k] for k in fields if k in FIELD_GROUP}
        if groups:
            new = validate_receipt(new, _transcript(new), only=groups)
        changed = changed or "approval_no" in fields
        out.append(new)
    if changed:
        out = mark_dup_approval(out)
    return clear_warnings(out, overrides)
