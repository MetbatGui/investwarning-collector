"""collect_today()가 CollectionResult 값 객체를 반환하는지 검증 (orchestration_guide.md §1, §2.2).

네트워크(KRX 실 호출) 없이, fetch_investment_warning_stocks만 monkeypatch해서
오프라인으로 조기 반환 경로를 고정한다.
"""

from investment_hub.application import services as services_module
from investment_hub.application.services import WarningCollectionService
from investment_hub.domain.models import CollectionResult
from investment_hub.infrastructure.adapters.local_storage_adapter import LocalStorageAdapter
from investment_hub.infrastructure.adapters.parquet_repository_adapter import ParquetRepositoryAdapter


def _make_service():
    repo = ParquetRepositoryAdapter(base_dir="tests/dummy_parquet")
    storage = LocalStorageAdapter()
    return WarningCollectionService(
        repository=repo,
        storage=storage,
        output_dir="tests/dummy_out",
        max_warning_days=60,
    )


def test_collect_today_returns_collection_result_when_no_stocks_found(monkeypatch):
    monkeypatch.setattr(services_module, "fetch_investment_warning_stocks", lambda *a, **k: [])

    service = _make_service()
    result = service.collect_today(end_date="2026-01-15", days=1, include_active=True)

    assert isinstance(result, CollectionResult)
    assert result.success is False
    assert result.discovered == 0
    assert result.reason


def test_collect_today_returns_collection_result_on_scraper_error(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(services_module, "fetch_investment_warning_stocks", _raise)

    service = _make_service()
    result = service.collect_today(end_date="2026-01-15", days=1, include_active=True)

    assert isinstance(result, CollectionResult)
    assert result.success is False
    assert "network down" in result.reason
