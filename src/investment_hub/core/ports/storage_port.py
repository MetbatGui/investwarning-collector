from abc import ABC, abstractmethod

import openpyxl
import pandas as pd


class StoragePort(ABC):
    """저장소 인터페이스 (Port)"""

    @abstractmethod
    def save_dataframe_excel(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """DataFrame을 Excel 파일 지정된 경로에 저장합니다."""
        pass

    @abstractmethod
    def save_dataframe_csv(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """DataFrame을 CSV 파일로 지정된 경로에 저장합니다."""
        pass

    @abstractmethod
    def save_parquet(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """DataFrame을 Parquet 파일로 지정된 경로에 저장합니다."""
        pass

    @abstractmethod
    def load_parquet(self, path: str, **kwargs) -> pd.DataFrame:
        """지정된 경로에서 Parquet 파일을 읽어 DataFrame으로 반환합니다."""
        pass

    @abstractmethod
    def save_workbook(self, book: openpyxl.Workbook, path: str) -> bool:
        """openpyxl Workbook 객체를 지정된 경로에 저장합니다."""
        pass

    @abstractmethod
    def load_workbook(self, path: str) -> openpyxl.Workbook | None:
        """지정된 경로에서 Excel Workbook을 로드합니다."""
        pass

    @abstractmethod
    def load_dataframe(self, path: str, sheet_name: str | None = None, **kwargs) -> pd.DataFrame:
        """지정된 경로에서 DataFrame을 로드합니다."""
        pass

    @abstractmethod
    def path_exists(self, path: str) -> bool:
        """지정된 경로에 파일이나 디렉토리가 존재하는지 확인합니다."""
        pass

    @abstractmethod
    def ensure_directory(self, path: str) -> bool:
        """지정된 디렉토리가 존재하도록 보장합니다 (없으면 생성)."""
        pass

    @abstractmethod
    def get_file(self, path: str) -> bytes | None:
        """파일의 내용을 바이트로 읽어옵니다."""
        pass

    @abstractmethod
    def put_file(self, path: str, data: bytes) -> bool:
        """바이트 데이터를 파일로 저장합니다."""
        pass
