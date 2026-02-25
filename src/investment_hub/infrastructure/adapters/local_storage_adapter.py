from pathlib import Path

import openpyxl
import pandas as pd

from investment_hub.core.ports.storage_port import StoragePort


class LocalStorageAdapter(StoragePort):
    """로컬 파일 시스템 저장소 어댑터"""

    def __init__(self, base_path: str = "."):
        self.base_path = Path(base_path)

    def _full_path(self, path: str) -> Path:
        return self.base_path / path

    def save_dataframe_excel(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """DataFrame을 Excel 파일로 로컬에 원자적으로 저장합니다."""
        full_path = self._full_path(path)
        self.ensure_directory(str(full_path.parent))

        tmp_path = full_path.with_suffix(".tmp.xlsx")
        try:
            df.to_excel(str(tmp_path), **kwargs)
            if full_path.exists():
                try:
                    tmp_path.replace(full_path)
                except PermissionError:
                    backup_path = full_path.parent / f"{full_path.stem}_new.xlsx"
                    tmp_path.replace(backup_path)
                    print(f"[LocalStorage] [WARN] {full_path.name} 사용 중. {backup_path.name}로 저장.")
            else:
                tmp_path.replace(full_path)
            return True
        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            print(f"[LocalStorage] [ERROR] Excel 저장 실패 ({path}): {e}")
            return False

    def save_dataframe_csv(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """DataFrame을 CSV 파일로 로컬에 원자적으로 저장합니다."""
        full_path = self._full_path(path)
        self.ensure_directory(str(full_path.parent))

        tmp_path = full_path.with_suffix(".tmp.csv")
        try:
            # 기본 인코딩 utf-8-sig (엑셀 호환성)
            kwargs.setdefault("encoding", "utf-8-sig")
            kwargs.setdefault("index", False)
            df.to_csv(str(tmp_path), **kwargs)
            tmp_path.replace(full_path)
            return True
        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            print(f"[LocalStorage] [ERROR] CSV 저장 실패 ({path}): {e}")
            return False

    def save_workbook(self, book: openpyxl.Workbook, path: str) -> bool:
        """Workbook 객체를 로컬에 원자적으로 저장합니다."""
        full_path = self._full_path(path)
        self.ensure_directory(str(full_path.parent))

        tmp_path = full_path.with_suffix(".tmp.xlsx")
        try:
            book.save(str(tmp_path))
            if full_path.exists():
                try:
                    tmp_path.replace(full_path)
                except PermissionError:
                    backup_path = full_path.parent / f"{full_path.stem}_new.xlsx"
                    tmp_path.replace(backup_path)
                    print(f"[LocalStorage] [WARN] {full_path.name} 사용 중. {backup_path.name}로 저장.")
            else:
                tmp_path.replace(full_path)
            return True
        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            print(f"[LocalStorage] [ERROR] Workbook 저장 실패 ({path}): {e}")
            return False

    def load_workbook(self, path: str) -> openpyxl.Workbook | None:
        """로컬에서 Workbook 로드"""
        full_path = self._full_path(path)
        if not full_path.exists():
            return None
        try:
            return openpyxl.load_workbook(str(full_path))
        except Exception as e:
            print(f"[LocalStorage] [ERROR] Workbook 로드 실패 ({path}): {e}")
            return None

    def load_dataframe(self, path: str, sheet_name: str | None = None, **kwargs) -> pd.DataFrame:
        """로컬에서 DataFrame 로드 (CSV 또는 Excel)"""
        full_path = self._full_path(path)
        if not full_path.exists():
            return pd.DataFrame()

        try:
            if full_path.suffix.lower() == ".csv":
                return pd.read_csv(str(full_path), **kwargs)
            else:
                target_sheet = 0 if sheet_name is None else sheet_name
                return pd.read_excel(str(full_path), sheet_name=target_sheet, **kwargs)
        except Exception as e:
            print(f"[LocalStorage] [ERROR] DataFrame 로드 실패 ({path}): {e}")
            return pd.DataFrame()

    def path_exists(self, path: str) -> bool:
        """로컬 경로 존재 여부 확인"""
        return self._full_path(path).exists()

    def ensure_directory(self, path: str) -> bool:
        """로컬 디렉토리 생성 보장"""
        full_path = self._full_path(path)
        try:
            full_path.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"[LocalStorage] [ERROR] 디렉토리 생성 실패 ({path}): {e}")
            return False

    def get_file(self, path: str) -> bytes | None:
        """로컬 파일 바이트 읽기"""
        full_path = self._full_path(path)
        if not full_path.exists():
            return None
        try:
            return full_path.read_bytes()
        except Exception as e:
            print(f"[LocalStorage] [ERROR] 파일 읽기 실패 ({path}): {e}")
            return None

    def put_file(self, path: str, data: bytes) -> bool:
        """로컬 파일 바이트 저장"""
        full_path = self._full_path(path)
        self.ensure_directory(str(full_path.parent))
        try:
            full_path.write_bytes(data)
            return True
        except Exception as e:
            print(f"[LocalStorage] [ERROR] 파일 쓰기 실패 ({path}): {e}")
            return False
