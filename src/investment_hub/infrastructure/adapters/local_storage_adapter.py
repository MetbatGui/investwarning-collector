from pathlib import Path

import openpyxl
import pandas as pd

from investment_hub.core.ports.storage_port import StoragePort


class LocalStorageAdapter(StoragePort):
    """StoragePort 인터페이스를 로컬 파일 시스템 기반으로 구현한 어댑터 클래스.

    모든 쓰기 작업은 원자적(Atomic) 기록 방식(.tmp 파일 임시 저장 후 파일명 교체)을 사용하여
    실행 도중 중단 시 기존 데이터가 손상되지 않도록 보장합니다.
    """

    def __init__(self, base_path: str = "."):
        self.base_path = Path(base_path)

    def _full_path(self, path: str) -> Path:
        return self.base_path / path

    def save_dataframe_excel(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """주어진 DataFrame을 로컬 저장소에 Excel 포맷(.xlsx)으로 안전하게 저장합니다.

        .tmp 확장자로 임시 파일을 우선 작성한 뒤, 작성이 완료되면 원본 경로로 덮어씁니다.
        만료되거나 권한에 의해 덮어쓰기가 불가능한 경우 `_new.xlsx` 이름으로 백업본을 남깁니다.

        Args:
            df (pd.DataFrame): 엑셀로 기록할 데이터.
            path (str): 저장하려는 기준 상대 경로나 절대 경로.
            **kwargs: `df.to_excel()`에 전달할 추가 옵션.

        Returns:
            bool: 파일 I/O 성공 시 True, 경로 권한/쓰기 오류 등 실패 시 False.
        """
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
        """주어진 DataFrame을 로컬 저장소에 CSV 시스템 포맷으로 안전하게 저장합니다.

        기본적으로 한글 엑셀 호환성을 위해 `utf-8-sig` 인코딩을 적용합니다.

        Args:
            df (pd.DataFrame): CSV로 기록할 데이터.
            path (str): 저장하려는 경로.
            **kwargs: `df.to_csv()`에 전달할 추가 옵션.

        Returns:
            bool: I/O 오류 없이 정상 저장될 경우 True.
        """
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

    def save_parquet(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """주어진 DataFrame을 로컬 시스템에 pyarrow 엔진 기반 Parquet 파일로 저장합니다.

        Args:
            df (pd.DataFrame): Parquet으로 변환할 데이터.
            path (str): 출력될 타겟 경로.
            **kwargs: `df.to_parquet()`에 전달될 추가 옵션 매개변수.

        Returns:
            bool: 원자적 저장이 성공적으로 마치면 True 반환.
        """
        full_path = self._full_path(path)
        self.ensure_directory(str(full_path.parent))

        tmp_path = full_path.with_suffix(".tmp.parquet")
        try:
            df.to_parquet(str(tmp_path), engine="pyarrow", **kwargs)
            tmp_path.replace(full_path)
            return True
        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            print(f"[LocalStorage] [ERROR] Parquet 저장 실패 ({path}): {e}")
            return False

    def save_workbook(self, book: openpyxl.Workbook, path: str) -> bool:
        """openpyxl Workbook 인스턴스를 지정한 로컬 경로에 원자적(Atomic)으로 저장합니다.

        Args:
            book (openpyxl.Workbook): 저장할 메모리 상의 엑셀 객체.
            path (str): 디스크에 최종 기록될 경로.

        Returns:
            bool: 안전하게 물리적 파일로 출력이 완료되면 True.
        """
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
        """지정된 로컬 경로의 엑셀 파일을 읽어와 파싱된 Workbook 인스턴스를 반환합니다.

        Args:
            path (str): 읽어올 .xlsx 파일 경로.

        Returns:
            openpyxl.Workbook | None: 파싱 성공 시 워크북 객체, 스트림 오류나 파일이 없으면 None.
        """
        full_path = self._full_path(path)
        if not full_path.exists():
            return None
        try:
            return openpyxl.load_workbook(str(full_path))
        except Exception as e:
            print(f"[LocalStorage] [ERROR] Workbook 로드 실패 ({path}): {e}")
            return None

    def load_dataframe(self, path: str, sheet_name: str | None = None, **kwargs) -> pd.DataFrame:
        """주어진 로컬 경로의 파일(CSv 혹은 Excel)을 데이터프레임으로 제공합니다.

        Args:
            path (str): 파싱 가능한 대상 파일 절대/상대 경로.
            sheet_name (str | None): .xlsx일 경우 읽어들일 시트명. (단일 시트라면 0으로 대체)
            **kwargs: `read_csv`나 `read_excel`에 추가 주입할 옵션.

        Returns:
            pd.DataFrame: 파일 내용이 담긴 데이터프레임. 파일 누락/오류인 경우 빈 DataFrame 반환.
        """
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

    def load_parquet(self, path: str, **kwargs) -> pd.DataFrame:
        """주어진 로컬 경로의 Parquet 파일을 읽어와 데이터프레임으로 렌더링합니다.

        Args:
            path (str): 접근할 Parquet 데이터 파일 위치.
            **kwargs: `read_parquet` 인자.

        Returns:
            pd.DataFrame: 데이터 로드 성공 시 DataFrame 리턴, 실패 시 빈 객체 리턴.
        """
        full_path = self._full_path(path)
        if not full_path.exists():
            return pd.DataFrame()

        try:
            return pd.read_parquet(str(full_path), engine="pyarrow", **kwargs)
        except Exception as e:
            print(f"[LocalStorage] [ERROR] Parquet 로드 실패 ({path}): {e}")
            return pd.DataFrame()

    def path_exists(self, path: str) -> bool:
        """입력받은 로컬 경로(파일이나 디렉토리)가 물리적으로 존재하는지 확인합니다.

        Args:
            path (str): 검사할 타겟 경로.

        Returns:
            bool: 해당 위치에 항목이 존재할 경우 True.
        """
        return self._full_path(path).exists()

    def ensure_directory(self, path: str) -> bool:
        """명시된 디렉토리 경로가 존재하지 않는다면 상위 폴더까지 포함해 새로 구축합니다.

        Args:
            path (str): 보장되어야 하는 타겟 디렉토리 경로.

        Returns:
            bool: 무결하게 존재가 확보되었을 시 True, mkdir 실패 시 False.
        """
        full_path = self._full_path(path)
        try:
            full_path.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"[LocalStorage] [ERROR] 디렉토리 생성 실패 ({path}): {e}")
            return False

    def get_file(self, path: str) -> bytes | None:
        """로컬 스토리지에 존재하는 파일을 찾아 순수 바이너리 스트림으로 공급합니다.

        Args:
            path (str): 원시 파일을 읽어올 경로 위치.

        Returns:
            bytes | None: 성공적으로 수신한 바이트 데이터 형태의 콘텐츠, 접근 불가 시 None.
        """
        full_path = self._full_path(path)
        if not full_path.exists():
            return None
        try:
            return full_path.read_bytes()
        except Exception as e:
            print(f"[LocalStorage] [ERROR] 파일 읽기 실패 ({path}): {e}")
            return None

    def put_file(self, path: str, data: bytes) -> bool:
        """전달받은 바이트 콘텐츠 조각을 로컬 파일 시스템 내 해당 맵핑 파일명으로 기록합니다.

        Args:
            path (str): 데이터가 작성될(덮어쓰여질) 최종 파일 위치 지정자.
            data (bytes): 인코딩된 스트림 데이터.

        Returns:
            bool: 모든 바이트 스트림 쓰기가 성공적으로 디스크에 완료된 개별 이벤트 결과 반환.
        """
        full_path = self._full_path(path)
        self.ensure_directory(str(full_path.parent))
        try:
            full_path.write_bytes(data)
            return True
        except Exception as e:
            print(f"[LocalStorage] [ERROR] 파일 쓰기 실패 ({path}): {e}")
            return False
