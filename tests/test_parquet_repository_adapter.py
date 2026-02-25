"""ParquetRepositoryAdapter 유닛 테스트."""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock
from investment_hub.infrastructure.adapters.parquet_repository_adapter import (
    ParquetRepositoryAdapter,
)

# ─────────────────────────────────────────────────────────────────────────────
# 테스트 픽스처
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path) -> ParquetRepositoryAdapter:
    """임시 디렉토리를 사용하는 ParquetRepositoryAdapter 픽스처."""
    return ParquetRepositoryAdapter(str(tmp_path / "parquet"))


def _make_stock(
    code: str = "005930",
    name: str = "삼성전자",
    market: str = "코스피",
    designation_date: datetime = datetime(2025, 1, 2),
    release_date: datetime | None = datetime(2025, 1, 30),
) -> InvestmentWarningStock:
    """테스트용 InvestmentWarningStock 생성 헬퍼."""
    return InvestmentWarningStock(
        code=code,
        name=name,
        market=market,
        designation_date=designation_date,
        release_date=release_date,
    )


def _make_prices(
    code: str = "005930",
    name: str = "삼성전자",
    dates: list[datetime] | None = None,
) -> list[DailyPriceData]:
    """테스트용 DailyPriceData 목록 생성 헬퍼."""
    if dates is None:
        dates = [datetime(2025, 1, 2), datetime(2025, 1, 3), datetime(2025, 1, 6)]
    return [
        DailyPriceData(code=code, name=name, date=d, close=70000 + i * 100, change_rate=round(0.5 * i, 2))
        for i, d in enumerate(dates)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# TestSaveAndLoadYear
# ─────────────────────────────────────────────────────────────────────────────


class TestSaveAndLoadYear:
    """save_year / load_year 왕복 테스트."""

    def test_roundtrip_single_stock(self, repo: ParquetRepositoryAdapter) -> None:
        """단일 종목 저장 후 로드 시 데이터가 완전히 복원되어야 한다."""
        stock = _make_stock()
        prices = _make_prices()

        repo.save_year(2025, [stock], {stock.code: prices})
        loaded_stocks, loaded_prices = repo.load_year(2025)

        assert len(loaded_stocks) == 1
        s = loaded_stocks[0]
        assert s.code == stock.code
        assert s.name == stock.name
        assert s.market == stock.market
        assert s.designation_date.date() == stock.designation_date.date()
        assert s.release_date is not None
        assert s.release_date.date() == stock.release_date.date()  # type: ignore[union-attr]

        assert stock.code in loaded_prices
        assert len(loaded_prices[stock.code]) == len(prices)

    def test_roundtrip_active_stock(self, repo: ParquetRepositoryAdapter) -> None:
        """해제일 없는 활성 종목(release_date=None)이 올바르게 복원되어야 한다."""
        stock = _make_stock(release_date=None)
        prices = _make_prices()

        repo.save_year(2025, [stock], {stock.code: prices})
        loaded_stocks, _ = repo.load_year(2025)

        assert len(loaded_stocks) == 1
        assert loaded_stocks[0].release_date is None
        assert loaded_stocks[0].is_active is True

    def test_type_preservation_datetime(self, repo: ParquetRepositoryAdapter) -> None:
        """designation_date와 date 컬럼이 datetime 타입으로 복원되어야 한다."""
        stock = _make_stock()
        prices = _make_prices()

        repo.save_year(2025, [stock], {stock.code: prices})
        loaded_stocks, loaded_prices = repo.load_year(2025)

        assert isinstance(loaded_stocks[0].designation_date, datetime)
        price_list = loaded_prices[stock.code]
        assert all(isinstance(p.date, datetime) for p in price_list)

    def test_type_preservation_numeric(self, repo: ParquetRepositoryAdapter) -> None:
        """close(float)와 change_rate(float)가 숫자 타입으로 복원되어야 한다."""
        stock = _make_stock()
        prices = _make_prices()

        repo.save_year(2025, [stock], {stock.code: prices})
        _, loaded_prices = repo.load_year(2025)

        price_list = loaded_prices[stock.code]
        assert all(isinstance(p.close, float) for p in price_list)
        assert all(isinstance(p.change_rate, float) for p in price_list)

    def test_prices_sorted_by_date(self, repo: ParquetRepositoryAdapter) -> None:
        """로드된 시세 목록은 날짜 오름차순으로 정렬되어야 한다."""
        stock = _make_stock()
        dates = [datetime(2025, 1, 6), datetime(2025, 1, 2), datetime(2025, 1, 3)]
        prices = _make_prices(dates=dates)

        repo.save_year(2025, [stock], {stock.code: prices})
        _, loaded_prices = repo.load_year(2025)

        loaded_dates = [p.date for p in loaded_prices[stock.code]]
        assert loaded_dates == sorted(loaded_dates)

    def test_overwrites_existing_partition(self, repo: ParquetRepositoryAdapter) -> None:
        """save_year 재호출 시 기존 파티션을 완전히 대체해야 한다."""
        stock_a = _make_stock(code="000001", name="주식A")
        stock_b = _make_stock(code="000002", name="주식B")

        repo.save_year(2025, [stock_a], {stock_a.code: _make_prices(code="000001")})
        repo.save_year(2025, [stock_b], {stock_b.code: _make_prices(code="000002")})

        loaded_stocks, loaded_prices = repo.load_year(2025)

        codes = {s.code for s in loaded_stocks}
        assert "000001" not in codes
        assert "000002" in codes

    def test_empty_price_stock_excluded(self, repo: ParquetRepositoryAdapter) -> None:
        """시세 데이터가 없는 종목은 저장 시 제외되어야 한다."""
        stock = _make_stock()
        repo.save_year(2025, [stock], {})  # 시세 없음

        loaded_stocks, loaded_prices = repo.load_year(2025)
        assert len(loaded_stocks) == 0
        assert len(loaded_prices) == 0

    def test_load_nonexistent_year_returns_empty(self, repo: ParquetRepositoryAdapter) -> None:
        """존재하지 않는 연도 조회 시 ([], {})를 반환해야 한다."""
        stocks, prices = repo.load_year(9999)
        assert stocks == []
        assert prices == {}

    def test_multiple_stocks(self, repo: ParquetRepositoryAdapter) -> None:
        """여러 종목 저장 후 모두 정확하게 복원되어야 한다."""
        stock_a = _make_stock(code="000001", name="주식A", designation_date=datetime(2025, 1, 2))
        stock_b = _make_stock(code="000002", name="주식B", designation_date=datetime(2025, 2, 1))
        prices_a = _make_prices(code="000001")
        prices_b = _make_prices(code="000002")

        repo.save_year(2025, [stock_a, stock_b], {"000001": prices_a, "000002": prices_b})
        loaded_stocks, loaded_prices = repo.load_year(2025)

        assert len(loaded_stocks) == 2
        loaded_codes = {s.code for s in loaded_stocks}
        assert loaded_codes == {"000001", "000002"}
        assert len(loaded_prices["000001"]) == len(prices_a)
        assert len(loaded_prices["000002"]) == len(prices_b)


# ─────────────────────────────────────────────────────────────────────────────
# TestYearExists
# ─────────────────────────────────────────────────────────────────────────────


class TestYearExists:
    """year_exists 테스트."""

    def test_returns_false_before_save(self, repo: ParquetRepositoryAdapter) -> None:
        """저장 전에는 False를 반환해야 한다."""
        assert repo.year_exists(2025) is False

    def test_returns_true_after_save(self, repo: ParquetRepositoryAdapter) -> None:
        """저장 후에는 True를 반환해야 한다."""
        stock = _make_stock()
        repo.save_year(2025, [stock], {stock.code: _make_prices()})
        assert repo.year_exists(2025) is True

    def test_other_year_unaffected(self, repo: ParquetRepositoryAdapter) -> None:
        """2025년 저장이 2024년 year_exists에 영향을 주지 않아야 한다."""
        stock = _make_stock()
        repo.save_year(2025, [stock], {stock.code: _make_prices()})
        assert repo.year_exists(2024) is False


# ─────────────────────────────────────────────────────────────────────────────
# TestAppendYear
# ─────────────────────────────────────────────────────────────────────────────


class TestAppendYear:
    """append_year 증분 병합 테스트."""

    def test_append_new_dates(self, repo: ParquetRepositoryAdapter) -> None:
        """새 날짜 시세를 추가하면 기존 시세와 합쳐져야 한다."""
        stock = _make_stock()
        prices_day1 = _make_prices(dates=[datetime(2025, 1, 2)])
        prices_day2 = _make_prices(dates=[datetime(2025, 1, 3)])

        repo.save_year(2025, [stock], {stock.code: prices_day1})
        repo.append_year(2025, [stock], {stock.code: prices_day2})

        _, loaded_prices = repo.load_year(2025)
        loaded_dates = {p.date.date() for p in loaded_prices[stock.code]}
        assert datetime(2025, 1, 2).date() in loaded_dates
        assert datetime(2025, 1, 3).date() in loaded_dates

    def test_duplicate_rows_deduplicated(self, repo: ParquetRepositoryAdapter) -> None:
        """동일 (code, designation_date, date)의 중복 행은 제거되어야 한다."""
        stock = _make_stock()
        dates = [datetime(2025, 1, 2)]
        prices = _make_prices(dates=dates)

        repo.save_year(2025, [stock], {stock.code: prices})
        repo.append_year(2025, [stock], {stock.code: prices})  # 동일 데이터 재추가

        _, loaded_prices = repo.load_year(2025)
        assert len(loaded_prices[stock.code]) == 1

    def test_new_data_wins_on_conflict(self, repo: ParquetRepositoryAdapter) -> None:
        """중복 키 충돌 시 새 데이터(가격)로 덮어써야 한다."""
        stock = _make_stock()
        date = datetime(2025, 1, 2)

        original = [DailyPriceData(code=stock.code, name=stock.name, date=date, close=70000, change_rate=0.5)]
        updated = [DailyPriceData(code=stock.code, name=stock.name, date=date, close=72000, change_rate=1.5)]

        repo.save_year(2025, [stock], {stock.code: original})
        repo.append_year(2025, [stock], {stock.code: updated})

        _, loaded_prices = repo.load_year(2025)
        assert len(loaded_prices[stock.code]) == 1
        assert loaded_prices[stock.code][0].close == pytest.approx(72000.0)

    def test_append_to_nonexistent_partition(self, repo: ParquetRepositoryAdapter) -> None:
        """파티션이 없을 때 append_year는 save_year와 동일하게 동작해야 한다."""
        stock = _make_stock()
        prices = _make_prices()

        repo.append_year(2025, [stock], {stock.code: prices})

        assert repo.year_exists(2025)
        loaded_stocks, loaded_prices = repo.load_year(2025)
        assert len(loaded_stocks) == 1
        assert len(loaded_prices[stock.code]) == len(prices)

    def test_append_new_stock(self, repo: ParquetRepositoryAdapter) -> None:
        """기존 파티션에 없던 새 종목을 append하면 추가되어야 한다."""
        stock_a = _make_stock(code="000001", name="주식A")
        stock_b = _make_stock(code="000002", name="주식B")

        repo.save_year(2025, [stock_a], {stock_a.code: _make_prices(code="000001")})
        repo.append_year(2025, [stock_b], {stock_b.code: _make_prices(code="000002")})

        loaded_stocks, loaded_prices = repo.load_year(2025)
        codes = {s.code for s in loaded_stocks}
        assert "000001" in codes
        assert "000002" in codes


# ─────────────────────────────────────────────────────────────────────────────
# TestListAvailableYears
# ─────────────────────────────────────────────────────────────────────────────


class TestListAvailableYears:
    """list_available_years 테스트."""

    def test_empty_dir_returns_empty(self, repo: ParquetRepositoryAdapter) -> None:
        """저장된 파티션이 없으면 빈 리스트를 반환해야 한다."""
        assert repo.list_available_years() == []

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        """base_dir 자체가 없을 때도 빈 리스트를 반환해야 한다."""
        repo = ParquetRepositoryAdapter(str(tmp_path / "does_not_exist"))
        assert repo.list_available_years() == []

    def test_returns_sorted_years(self, repo: ParquetRepositoryAdapter) -> None:
        """여러 연도 저장 시 오름차순으로 정렬된 리스트를 반환해야 한다."""
        stock = _make_stock()
        for year in [2023, 2025, 2021]:
            repo.save_year(year, [stock], {stock.code: _make_prices()})

        assert repo.list_available_years() == [2021, 2023, 2025]

    def test_tmp_files_excluded(self, repo: ParquetRepositoryAdapter) -> None:
        """임시 파일(.tmp.parquet)은 목록에 포함되지 않아야 한다."""
        # 강제로 tmp 파일 생성
        repo._base_dir.mkdir(parents=True, exist_ok=True)
        (repo._base_dir / "2025.tmp.parquet").write_bytes(b"")

        assert repo.list_available_years() == []


# ─────────────────────────────────────────────────────────────────────────────
# TestAtomicSave
# ─────────────────────────────────────────────────────────────────────────────


class TestAtomicSave:
    """원자적 저장 테스트."""

    def test_tmp_file_removed_on_success(self, repo: ParquetRepositoryAdapter) -> None:
        """저장 성공 후 임시 파일이 남아 있지 않아야 한다."""
        stock = _make_stock()
        repo.save_year(2025, [stock], {stock.code: _make_prices()})

        tmp_files = list(repo._base_dir.glob("*.tmp.parquet"))
        assert tmp_files == []

    def test_original_preserved_on_simulated_failure(
        self, repo: ParquetRepositoryAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """저장 실패 시 기존 파티션이 보존되어야 한다."""
        # 1차 저장 성공
        stock = _make_stock()
        prices_original = _make_prices(dates=[datetime(2025, 1, 2)])
        repo.save_year(2025, [stock], {stock.code: prices_original})

        # os.replace를 실패하도록 패치
        import os

        def fail_replace(src: str, dst: str) -> None:
            raise OSError("Simulated disk failure")

        monkeypatch.setattr(os, "replace", fail_replace)

        prices_new = _make_prices(dates=[datetime(2025, 1, 3)])
        with pytest.raises(OSError):
            repo.save_year(2025, [stock], {stock.code: prices_new})

        # 기존 파티션이 보존되어야 함
        _, loaded_prices = repo.load_year(2025)
        loaded_dates = {p.date.date() for p in loaded_prices[stock.code]}
        assert datetime(2025, 1, 2).date() in loaded_dates
        assert datetime(2025, 1, 3).date() not in loaded_dates

    def test_parquet_file_created(self, repo: ParquetRepositoryAdapter) -> None:
        """save_year 호출 후 .parquet 파일이 실제로 생성되어야 한다."""
        stock = _make_stock()
        repo.save_year(2025, [stock], {stock.code: _make_prices()})

        assert (repo._base_dir / "2025.parquet").exists()

    def test_parquet_readable_by_pandas(self, repo: ParquetRepositoryAdapter) -> None:
        """저장된 Parquet 파일이 pandas로 직접 읽힐 수 있어야 한다."""
        stock = _make_stock()
        repo.save_year(2025, [stock], {stock.code: _make_prices()})

        df = pd.read_parquet(str(repo._base_dir / "2025.parquet"))
        assert not df.empty
        assert "code" in df.columns
        assert "date" in df.columns
        assert "close" in df.columns
