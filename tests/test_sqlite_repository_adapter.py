"""SqliteRepositoryAdapter 유닛 테스트 (db_ssot_guide.md 체크리스트 대응).

- PK: (code, designation_date, date) - 정정 불가능한 자연키 조합 (§2)
- 쓰기: SQL upsert, 트랜잭션 (§5, §7)
- year_exists/list_available_years는 기존 ParquetRepositoryAdapter와 동일 계약
"""

from datetime import datetime

from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock
from investment_hub.infrastructure.adapters.sqlite_repository_adapter import SqliteRepositoryAdapter


def _stock(code, name="테스트", market="코스피", desig="2026-01-02", release="2026-01-10"):
    return InvestmentWarningStock(
        code=code,
        name=name,
        market=market,
        designation_date=datetime.strptime(desig, "%Y-%m-%d"),
        release_date=datetime.strptime(release, "%Y-%m-%d") if release else None,
    )


def _price(code, name, date_str, close, rate):
    return DailyPriceData(code=code, name=name, date=datetime.strptime(date_str, "%Y-%m-%d"), close=close, change_rate=rate)


def test_save_year_then_load_year_roundtrip(tmp_path):
    repo = SqliteRepositoryAdapter(base_dir=str(tmp_path))
    stock = _stock("005930")
    prices = {"005930": [_price("005930", "테스트", "2026-01-02", 70000, 0.5), _price("005930", "테스트", "2026-01-03", 71000, 1.4)]}

    repo.save_year(2026, [stock], prices)
    loaded_stocks, loaded_prices = repo.load_year(2026)

    assert len(loaded_stocks) == 1
    assert loaded_stocks[0].code == "005930"
    assert len(loaded_prices["005930"]) == 2
    assert loaded_prices["005930"][0].close == 70000


def test_load_year_returns_empty_when_year_missing(tmp_path):
    repo = SqliteRepositoryAdapter(base_dir=str(tmp_path))
    stocks, prices = repo.load_year(2099)
    assert stocks == []
    assert prices == {}


def test_year_exists_and_list_available_years(tmp_path):
    repo = SqliteRepositoryAdapter(base_dir=str(tmp_path))
    assert repo.year_exists(2026) is False

    repo.save_year(2026, [_stock("005930")], {"005930": [_price("005930", "테스트", "2026-01-02", 70000, 0.5)]})
    repo.save_year(2025, [_stock("000001", desig="2025-03-01", release="2025-03-10")], {"000001": [_price("000001", "테스트", "2025-03-01", 5000, 1.0)]})

    assert repo.year_exists(2026) is True
    assert repo.list_available_years() == [2025, 2026]


def test_append_year_upserts_by_code_designation_date_and_date(tmp_path):
    """같은 (code, designation_date, date) 키는 새 데이터로 덮어쓴다 - release_date 동기화 시나리오."""
    repo = SqliteRepositoryAdapter(base_dir=str(tmp_path))
    stock_v1 = _stock("005930", release="2026-01-10")
    repo.save_year(2026, [stock_v1], {"005930": [_price("005930", "테스트", "2026-01-02", 70000, 0.5)]})

    # release_date가 갱신된 채로 재수집(append) - 동일 (code, designation_date) 메타는 새 값으로 갱신
    stock_v2 = _stock("005930", release="2026-01-07")
    repo.append_year(2026, [stock_v2], {"005930": [_price("005930", "테스트", "2026-01-02", 70000, 0.5)]})

    stocks, prices = repo.load_year(2026)
    assert len(stocks) == 1
    assert stocks[0].release_date.strftime("%Y-%m-%d") == "2026-01-07"
    # 동일 (code, designation_date, date) 가격 행은 1건으로 유지 (중복 저장 안 됨)
    assert len(prices["005930"]) == 1


def test_append_year_adds_new_price_rows_without_touching_existing(tmp_path):
    repo = SqliteRepositoryAdapter(base_dir=str(tmp_path))
    stock = _stock("005930")
    repo.save_year(2026, [stock], {"005930": [_price("005930", "테스트", "2026-01-02", 70000, 0.5)]})

    repo.append_year(2026, [stock], {"005930": [_price("005930", "테스트", "2026-01-03", 71000, 1.4)]})

    _, prices = repo.load_year(2026)
    assert len(prices["005930"]) == 2
    dates = sorted(p.date.strftime("%Y-%m-%d") for p in prices["005930"])
    assert dates == ["2026-01-02", "2026-01-03"]


def test_append_year_creates_year_if_missing(tmp_path):
    repo = SqliteRepositoryAdapter(base_dir=str(tmp_path))
    repo.append_year(2026, [_stock("005930")], {"005930": [_price("005930", "테스트", "2026-01-02", 70000, 0.5)]})

    assert repo.year_exists(2026) is True
