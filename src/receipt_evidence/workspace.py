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
