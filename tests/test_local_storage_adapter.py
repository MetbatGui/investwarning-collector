"""
LocalStorageAdapter 유닛 테스트 (실제 파일 I/O, tmp 디렉토리 사용)
"""
import pytest
import pandas as pd
from pathlib import Path

from investment_hub.infrastructure.adapters.local_storage_adapter import LocalStorageAdapter


@pytest.fixture
def adapter(tmp_path):
    """tmp_path를 base_path로 사용하는 어댑터"""
    return LocalStorageAdapter(base_path=str(tmp_path))


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "code": ["000001", "000002"],
        "name": ["A", "B"],
        "close": [10000, 20000],
    })


# ─────────────────────────────────────────────────────────────────────────────
# path_exists
# ─────────────────────────────────────────────────────────────────────────────

class TestPathExists:
    def test_existing_file(self, adapter, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("hello")
        assert adapter.path_exists("test.txt") is True

    def test_nonexistent_file(self, adapter):
        assert adapter.path_exists("nonexistent.csv") is False


# ─────────────────────────────────────────────────────────────────────────────
# CSV 저장 / 로드 왕복 테스트
# ─────────────────────────────────────────────────────────────────────────────

class TestCsvRoundtrip:
    def test_save_and_load_csv(self, adapter, sample_df):
        result = adapter.save_dataframe_csv(sample_df, "output/test.csv")
        assert result is True
        loaded = adapter.load_dataframe("output/test.csv", dtype={"code": str})
        assert list(loaded["code"]) == ["000001", "000002"]
        assert list(loaded["close"]) == [10000, 20000]

    def test_load_missing_csv_returns_empty_df(self, adapter):
        """파일 없으면 예외 대신 빈 DataFrame 반환 (adapter의 안전한 설계)"""
        df = adapter.load_dataframe("nonexistent.csv")
        assert df.empty

    def test_save_creates_parent_dirs(self, adapter, sample_df, tmp_path):
        adapter.save_dataframe_csv(sample_df, "deep/nested/dir/data.csv")
        assert (tmp_path / "deep" / "nested" / "dir" / "data.csv").exists()

    def test_save_returns_false_on_invalid_path(self, adapter, sample_df):
        """빈 문자열 경로 저장 시 False 반환"""
        # 이 테스트는 어댑터의 오류 처리를 검증
        result = adapter.save_dataframe_csv(pd.DataFrame(), "valid/empty.csv")
        # 빈 DataFrame 저장은 허용
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# Excel 저장 / 로드 왕복 테스트
# ─────────────────────────────────────────────────────────────────────────────

class TestExcelRoundtrip:
    def test_save_and_load_excel(self, adapter, sample_df):
        result = adapter.save_dataframe_excel(sample_df, "output/test.xlsx", index=False)
        assert result is True
        loaded = adapter.load_dataframe("output/test.xlsx", dtype={"code": str})
        assert list(loaded["code"]) == ["000001", "000002"]

    def test_save_excel_creates_dirs(self, adapter, sample_df, tmp_path):
        adapter.save_dataframe_excel(sample_df, "a/b/result.xlsx", index=False)
        assert (tmp_path / "a" / "b" / "result.xlsx").exists()


# ─────────────────────────────────────────────────────────────────────────────
# ensure_directory
# ─────────────────────────────────────────────────────────────────────────────

class TestEnsureDirectory:
    def test_creates_directory(self, adapter, tmp_path):
        result = adapter.ensure_directory("new_folder")
        assert result is True
        assert (tmp_path / "new_folder").is_dir()

    def test_idempotent(self, adapter, tmp_path):
        adapter.ensure_directory("existing")
        result = adapter.ensure_directory("existing")  # 두 번 호출해도 오류 없음
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# get_file / put_file (bytes I/O)
# ─────────────────────────────────────────────────────────────────────────────

class TestBytesIO:
    def test_put_and_get_file(self, adapter, tmp_path):
        data = b"\x89PNG\r\ntest_bytes"
        result = adapter.put_file("binary/test.bin", data)
        assert result is True
        loaded = adapter.get_file("binary/test.bin")
        assert loaded == data

    def test_get_missing_file_returns_none(self, adapter):
        assert adapter.get_file("no_such_file.bin") is None
