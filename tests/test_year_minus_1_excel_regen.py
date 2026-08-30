"""_sync_release_dates가 작년(year-1) DB를 바꾸면 그 해 엑셀도 재생성되는지 검증
(orchestration_guide.md §4.1 - DB만 바뀌고 산출물이 안 바뀌면 사람이 보는 결과가
낡은 채로 남는다).
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from investment_hub.application.services import WarningCollectionService
from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock
from investment_hub.infrastructure.adapters.local_storage_adapter import LocalStorageAdapter
from investment_hub.infrastructure.adapters.sqlite_repository_adapter import SqliteRepositoryAdapter


def _make_stock(code, name="테스트", market="코스피", desig="2025-12-20", release=None):
    return InvestmentWarningStock(
        code=code,
        name=name,
        market=market,
        designation_date=pd.to_datetime(desig),
        release_date=pd.to_datetime(release) if release else None,
    )


@pytest.fixture
def service(tmp_path):
    repo = SqliteRepositoryAdapter(base_dir=str(tmp_path / "db"))
    storage = LocalStorageAdapter()
    return WarningCollectionService(
        repository=repo,
        storage=storage,
        output_dir=str(tmp_path / "out"),
        max_warning_days=60,
    )


def test_sync_release_dates_reports_changed_years(service):
    """작년(2025) 저장분의 release_date가 KRX 최신 정보로 바뀌면 2025가 changed_years에 포함돼야 한다."""
    stock_2025 = _make_stock("000001", desig="2025-12-20", release=None)
    prices = {"000001": [DailyPriceData(code="000001", name="테스트", date=pd.to_datetime("2025-12-20"), close=1000, change_rate=1.0)]}
    service.repository.save_year(2025, [stock_2025], prices)

    krx_latest = [_make_stock("000001", desig="2025-12-20", release="2026-01-05")]

    changed_years = service._sync_release_dates(2026, krx_latest, MagicMock())

    assert 2025 in changed_years


def test_sync_release_dates_reports_no_change_when_nothing_updated(service):
    stock_2025 = _make_stock("000001", desig="2025-12-20", release="2026-01-05")
    prices = {"000001": [DailyPriceData(code="000001", name="테스트", date=pd.to_datetime("2025-12-20"), close=1000, change_rate=1.0)]}
    service.repository.save_year(2025, [stock_2025], prices)

    krx_latest = [_make_stock("000001", desig="2025-12-20", release="2026-01-05")]  # 동일

    changed_years = service._sync_release_dates(2026, krx_latest, MagicMock())

    assert changed_years == set()


def test_regenerate_excel_for_year_saves_workbook(service, monkeypatch):
    stock_2025 = _make_stock("000001", desig="2025-12-20", release="2026-01-05")
    prices = {"000001": [DailyPriceData(code="000001", name="테스트", date=pd.to_datetime("2025-12-20"), close=1000, change_rate=1.0)]}
    service.repository.save_year(2025, [stock_2025], prices)

    save_calls = []
    monkeypatch.setattr(service.storage, "save_workbook", lambda wb, path: save_calls.append(path) or True)

    service._regenerate_excel_for_year(2025, MagicMock())

    assert len(save_calls) == 1
    assert "2025" in save_calls[0]


def test_regenerate_excel_for_year_noops_when_year_has_no_data(service, monkeypatch):
    save_calls = []
    monkeypatch.setattr(service.storage, "save_workbook", lambda wb, path: save_calls.append(path) or True)

    service._regenerate_excel_for_year(2019, MagicMock())

    assert save_calls == []
