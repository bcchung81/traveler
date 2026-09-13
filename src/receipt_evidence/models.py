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
