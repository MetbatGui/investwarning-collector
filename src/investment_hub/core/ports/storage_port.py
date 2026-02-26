from abc import ABC, abstractmethod

import openpyxl
import pandas as pd


class StoragePort(ABC):
    """애플리케이션 내 원시 파일/데이터 입출력 처리를 담당하는 추상화 포트(인터페이스).

    기반 스토리지(로컬, 구글 드라이브, S3 등)의 구현체 형태와 무관하게
    일관된 DataFrame 및 바이너리 파일 입출력 스펙을 제공합니다.
    """

    @abstractmethod
    def save_dataframe_excel(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """주어진 pandas DataFrame을 지정된 경로의 Excel(.xlsx) 포맷으로 저장합니다.

        Args:
            df (pd.DataFrame): 저장할 데이터프레임.
            path (str): 저장될 타겟 파일의 전체 경로나 키.
            **kwargs: `to_excel` 처리에 전달할 추가 옵션 퍼포먼스.

        Returns:
            bool: 파일 저장이 성공하면 True, 실패하면 False.
        """
        pass

    @abstractmethod
    def save_dataframe_csv(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """주어진 pandas DataFrame을 지정된 경로의 CSV 포맷으로 저장합니다.

        Args:
            df (pd.DataFrame): 저장할 데이터프레임.
            path (str): 저장될 타겟 파일의 전체 경로.
            **kwargs: `to_csv` 처리에 전달할 추가 인자.

        Returns:
            bool: 성공적으로 저장되면 True, 그렇지 않으면 False.
        """
        pass

    @abstractmethod
    def save_parquet(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """주어진 pandas DataFrame을 압축된 Parquet 포맷으로 저장합니다.

        Args:
            df (pd.DataFrame): Parquet으로 변환할 데이터프레임.
            path (str): 파일 저장 위치.
            **kwargs: 엔진 설정 등 추가 옵션.

        Returns:
            bool: 저장 성공 시 True 반환.
        """
        pass

    @abstractmethod
    def load_parquet(self, path: str, **kwargs) -> pd.DataFrame:
        """지정된 경로에 위치한 Parquet 파일을 읽어와 데이터프레임으로 제공합니다.

        Args:
            path (str): 읽어올 Parquet 파일 경로.
            **kwargs: `read_parquet` 인자.

        Returns:
            pd.DataFrame: 파일에서 로드된 내용의 데이터프레임.
                파일이 없거나 읽기 실패 시엔 빈 DataFrame을 반환하거나 에러를 발생시킬 수 있습니다.
        """
        pass

    @abstractmethod
    def save_workbook(self, book: openpyxl.Workbook, path: str) -> bool:
        """openpyxl.Workbook 객체를 지정된 경로에 직접 바이너리로 저장합니다.

        서식이 적용된 엑셀 문서를 파일시스템 또는 원격 저장소에 쓸 때 사용합니다.

        Args:
            book (openpyxl.Workbook): 저장할 엑셀 워크북 메모리 객체.
            path (str): 저장할 대상 절대 또는 상대 경로.

        Returns:
            bool: 정상적으로 기록이 완료된 경우 True.
        """
        pass

    @abstractmethod
    def load_workbook(self, path: str) -> openpyxl.Workbook | None:
        """주어진 경로의 엑셀 파일을 읽어 openpyxl.Workbook 객체로 반환합니다.

        Args:
            path (str): 읽어올 엑셀 파일(.xlsx) 경로.

        Returns:
            openpyxl.Workbook | None: 파싱에 성공한 워크북 인스턴스. 파일이 없으면 None.
        """
        pass

    @abstractmethod
    def load_dataframe(self, path: str, sheet_name: str | None = None, **kwargs) -> pd.DataFrame:
        """엑셀, CSV 등 테이블 형태의 지정 경로 파일을 DataFrame으로 로드합니다.

        Args:
            path (str): 데이터 파일 경로.
            sheet_name (str | None): 엑셀인 경우 읽어들일 시트명. (CSV 등은 무시됨)
            **kwargs: 로드 시 필요한 추가 포맷 인자.

        Returns:
            pd.DataFrame: 데이터가 파싱된 프레임.
        """
        pass

    @abstractmethod
    def path_exists(self, path: str) -> bool:
        """주어진 파일 또는 디렉토리 경로가 해당 스토리지 상에 유효하게 존재하는지 점검합니다.

        Args:
            path (str): 존재 여부를 탐색할 대상 경로.

        Returns:
            bool: 존재하면 True, 없으면 False.
        """
        pass

    @abstractmethod
    def ensure_directory(self, path: str) -> bool:
        """주어진 디렉토리 경로가 존재하도록 보장하며, 없을 시 재귀적으로 생성합니다.

        Args:
            path (str): 보장할 타겟 디렉토리 경로.

        Returns:
            bool: 디렉토리가 성공적으로 마련되었거나 이미 존재하면 True.
        """
        pass

    @abstractmethod
    def get_file(self, path: str) -> bytes | None:
        """저장소의 단일 파일 내용을 바이트 스트림 형태로 수신합니다.

        Args:
            path (str): 대상 파일 경로.

        Returns:
            bytes | None: 파일 내부의 원시 바이트 데이터. 찾을 수 없다면 None 반환.
        """
        pass

    @abstractmethod
    def put_file(self, path: str, data: bytes) -> bool:
        """제공된 원시 바이트 데이터를 파일로 스토리지에 기록합니다.

        Args:
            path (str): 기록될 대상 파일 경로.
            data (bytes): 쓸 바이트 버퍼 데이터.

        Returns:
            bool: 파일 I/O 과정에 문제가 없었으면 True 반환.
        """
        pass
