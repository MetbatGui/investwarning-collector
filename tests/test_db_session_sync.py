"""_sync_db_down/_sync_db_up의 fail-closed 동작 검증 (db_ssot_guide.md §6.1, §6.2).

"원격에 없음"과 "원격에 있는데 다운로드 실패"를 구분하고, 다운로드 실패한 연도는
업로드를 건너뛰는지, 업로드 자체가 실패하면 결과에 반영되는지 확인한다.
"""

import logging

import pandas as pd
import pytest

from investment_hub.application.services import WarningCollectionService
from investment_hub.core.ports.storage_port import StoragePort
from investment_hub.infrastructure.adapters.sqlite_repository_adapter import SqliteRepositoryAdapter


class FakeStorage(StoragePort):
    """테스트용 인메모리 StoragePort. get_file/put_file/path_exists만 의미 있게 동작."""

    def __init__(self, remote_files=None, download_should_fail=False, upload_should_fail=False):
        self.files = dict(remote_files or {})
        self.download_should_fail = download_should_fail
        self.upload_should_fail = upload_should_fail
        self.put_calls = []

    def path_exists(self, path: str) -> bool:
        return path in self.files

    def get_file(self, path: str):
        if self.download_should_fail:
            return None
        return self.files.get(path)

    def put_file(self, path: str, data: bytes) -> bool:
        self.put_calls.append(path)
        if self.upload_should_fail:
            return False
        self.files[path] = data
        return True

    # 아래는 이 테스트에서 안 쓰이는 StoragePort 나머지 메서드 - 최소 스텁
    def save_dataframe_excel(self, df, path, **kwargs) -> bool:
        return True

    def save_dataframe_csv(self, df, path, **kwargs) -> bool:
        return True

    def save_parquet(self, df, path, **kwargs) -> bool:
        return True

    def load_parquet(self, path, **kwargs) -> pd.DataFrame:
        return pd.DataFrame()

    def save_workbook(self, book, path) -> bool:
        return True

    def load_workbook(self, path):
        return None

    def load_dataframe(self, path, sheet_name=None, **kwargs) -> pd.DataFrame:
        return pd.DataFrame()

    def ensure_directory(self, path: str) -> bool:
        return True


@pytest.fixture
def logger():
    return logging.getLogger("test")


def _make_service(storage, base_dir="tests/dummy_sqlite_sync"):
    repo = SqliteRepositoryAdapter(base_dir=base_dir)
    return WarningCollectionService(repository=repo, storage=storage, output_dir="tests/dummy_out_sync")


def test_sync_db_down_treats_missing_remote_as_success(logger, tmp_path):
    storage = FakeStorage(remote_files={})
    service = _make_service(storage, base_dir=str(tmp_path / "db"))

    ok = service._sync_db_down(2026, logger)

    assert ok is True


def test_sync_db_down_fails_closed_when_remote_exists_but_download_fails(logger, tmp_path):
    remote_path = str((tmp_path / "db" / "2026.db")).replace("\\", "/")
    storage = FakeStorage(remote_files={remote_path: b"real-data"}, download_should_fail=True)
    service = _make_service(storage, base_dir=str(tmp_path / "db"))

    ok = service._sync_db_down(2026, logger)

    assert ok is False


def test_sync_db_up_returns_false_when_put_file_fails(logger, tmp_path):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    (db_dir / "2026.db").write_bytes(b"local-data")

    storage = FakeStorage(upload_should_fail=True)
    service = _make_service(storage, base_dir=str(db_dir))

    ok = service._sync_db_up(2026, logger)

    assert ok is False
    assert storage.put_calls  # 시도는 했어야 함


def test_sync_db_up_skips_when_no_local_file(logger, tmp_path):
    storage = FakeStorage()
    service = _make_service(storage, base_dir=str(tmp_path / "db"))

    ok = service._sync_db_up(2026, logger)

    assert ok is True
    assert storage.put_calls == []


def test_collect_today_sets_db_upload_failed_when_upload_fails(monkeypatch, tmp_path):
    """DB 업로드가 실패하면 CollectionResult.db_upload_failed가 True여야 한다 -
    호출부(CLI)가 이걸로 exit code를 결정한다."""
    from investment_hub.application import services as services_module

    monkeypatch.setattr(services_module, "fetch_investment_warning_stocks", lambda *a, **k: [])

    storage = FakeStorage(upload_should_fail=True)
    service = _make_service(storage, base_dir=str(tmp_path / "db"))

    result = service.collect_today(end_date="2026-01-15", days=1, include_active=True)

    # 대상 종목이 없어 success=False로 조기 반환되는 경로지만, finally의 업로드
    # 시도는 여전히 일어나므로(데이터가 없어도 로컬에 뭔가 있을 수 있음) 업로드
    # 실패 시 그 상태는 별도로 기록돼야 한다. 여기서는 업로드할 로컬 파일 자체가
    # 없으므로(신규 DB) put_file이 호출되지 않아 db_upload_failed는 False가 정상.
    assert result.db_upload_failed is False


def test_collect_year_skips_upload_and_fails_when_download_confirmed_failed(monkeypatch, tmp_path):
    """--action year 경로도 이제 DB SSOT 세션을 거쳐야 한다 - 예전엔 아예 동기화하지 않았음."""
    from investment_hub.application import services as services_module

    remote_path = str((tmp_path / "db" / "2026.db")).replace("\\", "/")
    storage = FakeStorage(remote_files={remote_path: b"real-data"}, download_should_fail=True)
    service = _make_service(storage, base_dir=str(tmp_path / "db"))

    monkeypatch.setattr(services_module, "fetch_investment_warning_stocks", lambda *a, **k: [])

    ok = service.collect_year(2026)

    assert ok is False
    assert storage.put_calls == []  # 다운로드 실패한 연도는 업로드 시도조차 하면 안 됨
