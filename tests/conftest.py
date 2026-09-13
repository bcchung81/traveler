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
