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
    orientation: int = 1  # 원본 사진의 EXIF 방향값(1=그대로). 회전해 읽은 이미지는 추출 캐시 키를 따로 쓴다
    ingest_version: str = ""

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

class LawParams(BaseModel):
    """조문 본문에서 읽은 금액·비율. 문구가 바뀌어 못 읽은 값은 None으로 두고 해당 판정을 확인필요로 돌린다."""
    in_city_hours: float | None = None          # 제18조① 기준 시간(4시간)
    in_city_long: int | None = None             # 제18조① 기준 시간 이상(2만원)
    in_city_short: int | None = None            # 제18조① 기준 시간 미만(1만원)
    in_city_vehicle_cut: int | None = None      # 제18조① 공무용 차량 이용 시 감액(1만원)
    over_cap_ratio: tuple[int, int] | None = None       # 제16조① 숙박비 상한 초과 추가지급 한도(분자, 분모) = 10분의 3
    vehicle_daily_ratio: tuple[int, int] | None = None  # 제16조③ 공무용 차량 이용 시 일비 지급 비율 = 2분의 1

    @property
    def complete(self) -> bool:
        return all(v is not None for v in self.model_dump().values())

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
    params: LawParams = Field(default_factory=LawParams)

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
    law_notes: list[str] = Field(default_factory=list)

class BatchResult(BaseModel):
    run_id: str
    results: list[PipelineResult]
    warnings: list[str] = Field(default_factory=list)
    summary_md_path: str
    summary_json_path: str
    notices: list[str] = Field(default_factory=list)
