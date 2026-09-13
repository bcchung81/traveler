# tests/test_stations.py
import unicodedata
from receipt_evidence.stations import place_of

def test_station_and_airport_names_map_to_city():
    assert place_of("용산") == "서울" and place_of("용산역") == "서울" and place_of(" 서울 ") == "서울" and place_of("수서") == "서울"
    assert place_of("광주송정") == "광주" and place_of("동대구") == "대구" and place_of("천안아산(온양온천)") == "아산"
    assert place_of("나주") == "나주" and place_of("김포공항") == "서울" and place_of("제주") == "제주"
    assert place_of(unicodedata.normalize("NFD", "부산역")) == "부산"

def test_unknown_or_empty_names():
    assert place_of("어딘가역") is None and place_of("") is None and place_of(None) is None
