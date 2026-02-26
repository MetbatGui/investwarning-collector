"""Google Drive 저장소 어댑터"""

import io
import os

import openpyxl
import pandas as pd
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from investment_hub.core.ports.storage_port import StoragePort


class GoogleDriveAdapter(StoragePort):
    """Google Drive 클라우드를 전역 스토리지로 활용하는 외부 어댑터.

    Google Drive API (v3) 와 OAuth 2.0 Credentials를 이용하여 대상 파일(스프레드시트, Parquet 등)의
    청크 업로드 및 다운로드를 원활하게 수행할 수 있도록 StoragePort를 구체화합니다.
    """

    SCOPES = ["https://www.googleapis.com/auth/drive"]

    def __init__(
        self,
        token_file: str,
        root_folder_name: str = "KRX_Auto_Crawling_Data",
        root_folder_id: str | None = None,
        client_secret_file: str | None = None,
    ):
        """GoogleDriveAdapter 연결 관리 초기화.

        Args:
            token_file (str): OAuth 2.0 사용자 토큰 파일 상대/절대 경로.
            root_folder_name (str): 구글 드라이브 최상단 기준 대상 폴더명. 기본 "KRX_Auto_Crawling_Data"
            root_folder_id (str | None): 폴더명 대신 직접 ID로 맵핑해야 할 때 사용.
            client_secret_file (str | None): 자격 증명 갱신에 사용될 구글 클라우드 secret 파일의 위치.

        Raises:
            ValueError: `token_file` 값이 전액 부재일 때.
            FileNotFoundError: `token_file` 에 명시된 물리적 토큰 json 이 발견되지 않을 경우.
        """
        self.token_file = token_file
        self.client_secret_file = client_secret_file

        if not self.token_file:
            raise ValueError("token_file must be provided.")

        if not os.path.exists(self.token_file):
            raise FileNotFoundError(f"Token file not found: {self.token_file}")

        self.drive_service = self._authenticate()

        if root_folder_id:
            self.root_folder_id = root_folder_id
            print(f"[GoogleDrive] 초기화 완료 (지정된 Root ID: {self.root_folder_id})")
        else:
            self.root_folder_id = self._get_or_create_folder(root_folder_name)
            print(f"[GoogleDrive] 초기화 완료 (Root: {root_folder_name}, ID: {self.root_folder_id})")

    def _authenticate(self):
        """Google Drive API 인증 (OAuth 2.0 Token)."""
        try:
            creds = Credentials.from_authorized_user_file(self.token_file, self.SCOPES)

            # 토큰 만료 시 갱신 시도
            if creds and creds.expired and creds.refresh_token:
                print("[GoogleDrive] 토큰 만료, 갱신 시도...")
                creds.refresh(Request())

                # 갱신된 토큰 저장
                with open(self.token_file, "w") as token:
                    token.write(creds.to_json())

            return build("drive", "v3", credentials=creds)
        except Exception as e:
            raise RuntimeError(f"Google Drive 인증 실패: {e}") from e

    def _get_or_create_folder(self, folder_name: str, parent_id: str = "root") -> str:
        """폴더를 찾거나 생성합니다."""
        query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed = false"
        results = self.drive_service.files().list(q=query, fields="files(id, name)").execute()
        files = results.get("files", [])

        if files:
            return files[0]["id"]
        else:
            file_metadata = {
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            }
            file = self.drive_service.files().create(body=file_metadata, fields="id").execute()
            print(f"[GoogleDrive] 📁 폴더 생성: {folder_name} (ID: {file.get('id')})")
            return file.get("id")

    def _get_file_id(self, path: str) -> str | None:
        """경로(상대 경로)에 해당하는 파일/폴더의 ID를 찾습니다."""
        parts = path.strip("/").split("/")
        current_parent_id = self.root_folder_id

        for part in parts:
            query = f"name = '{part}' and '{current_parent_id}' in parents and trashed = false"
            results = self.drive_service.files().list(q=query, fields="files(id, mimeType)").execute()
            files = results.get("files", [])

            if not files:
                return None

            current_parent_id = files[0]["id"]

        return current_parent_id

    def _ensure_path_directories(self, path: str) -> str:
        """파일 경로의 상위 디렉토리들을 생성하고 마지막 부모 폴더 ID를 반환합니다."""
        parts = path.strip("/").split("/")
        dir_parts = parts[:-1]

        current_parent_id = self.root_folder_id
        for part in dir_parts:
            current_parent_id = self._get_or_create_folder(part, current_parent_id)

        return current_parent_id

    def save_dataframe_excel(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """DataFrame을 내부 io 버퍼로 Excel 엔진 변환 후 API를 거쳐 드라이브에 업로드합니다.

        Args:
            df (pd.DataFrame): 엑셀로 기록할 데이터 시퀀스 매핑.
            path (str): 루트를 기준으로 한 파일 상대 경로명.
            **kwargs: `df.to_excel()` 기록용 추가 옵션.

        Returns:
            bool: 원격 업로드 성공 시 True 반환.
        """
        try:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                df.to_excel(writer, **kwargs)
            output.seek(0)

            self._upload_file(output, path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            print(f"[GoogleDrive] [OK] Excel 업로드: {path}")
            return True
        except Exception as e:
            print(f"[GoogleDrive] [Error] Excel 업로드 실패 ({path}): {e}")
            return False

    def save_dataframe_csv(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """DataFrame을 내부 텍스트 버퍼 변환 후 외부 드라이브 경로에 CSV로 업로드 보관합니다.

        Args:
            df (pd.DataFrame): 텍스트 데이터.
            path (str): 원격에 생성될 기준 파일명과 상대경로.
            **kwargs: `df.to_csv()` 추가 출력 규격 옵션.

        Returns:
            bool: 에러 없이 트랜잭션이 종료되면 True.
        """
        try:
            encoding = kwargs.pop("encoding", "utf-8-sig")  # utf-8-sig 기본값 사용 호환성

            output_str = io.StringIO()
            # index 기본값 False 호환성
            if "index" not in kwargs:
                kwargs["index"] = False

            df.to_csv(output_str, **kwargs)
            output_bytes = io.BytesIO(output_str.getvalue().encode(encoding))

            self._upload_file(output_bytes, path, "text/csv")
            print(f"[GoogleDrive] [OK] CSV 업로드: {path} (encoding: {encoding})")
            return True
        except Exception as e:
            print(f"[GoogleDrive] [Error] CSV 업로드 실패 ({path}): {e}")
            return False

    def save_parquet(self, df: pd.DataFrame, path: str, **kwargs) -> bool:
        """DataFrame을 Parquet 규격으로 원격 클라우드 드라이브에 직접 업로드 기록합니다.

        Args:
            df (pd.DataFrame): 압축 저장할 판다스 객체.
            path (str): 대상 디렉토리/파일명 조합 문자열 지시자.
            **kwargs: `engine='pyarrow'`와 수반되는 여타의 옵션들.

        Returns:
            bool: 전송 문제 없이 완료되었으면 True.
        """
        try:
            output = io.BytesIO()
            df.to_parquet(output, engine="pyarrow", **kwargs)
            output.seek(0)

            # parquet의 공식 mime type이 없을 경우 기본 바이너리로 처리
            self._upload_file(output, path, "application/octet-stream")
            print(f"[GoogleDrive] [OK] Parquet 업로드: {path}")
            return True
        except Exception as e:
            print(f"[GoogleDrive] [Error] Parquet 업로드 실패 ({path}): {e}")
            return False

    def save_workbook(self, book: openpyxl.Workbook, path: str) -> bool:
        """엑셀 Workbook 객체를 스트림 버퍼화 하여 드라이브에 .xlsx mime 타입으로 업로드합니다.

        Args:
            book (openpyxl.Workbook): 서식이 구비된 엑셀 매핑 객체.
            path (str): 원격 위치 이름 규격.

        Returns:
            bool: 청크 전송과 ID 갱신 무관하게 최종 확인된 응답일 때 True.
        """
        try:
            output = io.BytesIO()
            book.save(output)
            output.seek(0)

            self._upload_file(output, path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            print(f"[GoogleDrive] [OK] Workbook 업로드: {path}")
            return True
        except Exception as e:
            print(f"[GoogleDrive] [Error] Workbook 업로드 실패 ({path}): {e}")
            return False

    def _upload_file(self, data: io.BytesIO, path: str, mime_type: str):
        """파일 업로드 (생성 또는 업데이트)."""
        filename = os.path.basename(path)
        parent_id = self._ensure_path_directories(path)

        query = f"name = '{filename}' and '{parent_id}' in parents and trashed = false"
        results = self.drive_service.files().list(q=query, fields="files(id)").execute()
        files = results.get("files", [])

        media = MediaIoBaseUpload(data, mimetype=mime_type, resumable=True)

        if files:
            file_id = files[0]["id"]
            self.drive_service.files().update(fileId=file_id, media_body=media).execute()
        else:
            file_metadata = {"name": filename, "parents": [parent_id]}
            self.drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()

    def load_workbook(self, path: str) -> openpyxl.Workbook | None:
        """구글 드라이브 내 지정 파일을 다운로드 받아 파싱이 완료된 Workbook 형식으로 전달합니다.

        Args:
            path (str): 조회/다운로드 할 .xlsx 대응 파일 상대경로.

        Returns:
            openpyxl.Workbook | None: 성공적으로 파싱된 엑셀 워크북, 미존재 시 None.
        """
        try:
            file_id = self._get_file_id(path)
            if not file_id:
                print(f"[GoogleDrive] [Warn] 파일 없음: {path}")
                return None

            request = self.drive_service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()

            fh.seek(0)
            return openpyxl.load_workbook(fh)
        except Exception as e:
            print(f"[GoogleDrive] [Error] Workbook 로드 실패 ({path}): {e}")
            return None

    def path_exists(self, path: str) -> bool:
        """경로 존재 여부 확인."""
        return self._get_file_id(path) is not None

    def ensure_directory(self, path: str) -> bool:
        """디렉토리 생성."""
        try:
            # 부모 디렉토리 생성 로직 재사용. 구드에서는 폴더-파일 구분이 mimeType으로 되지만
            # 여기서는 마지막 파트까지 폴더로 취급하여 생성함
            parts = path.strip("/").split("/")
            current_parent_id = self.root_folder_id
            for part in parts:
                current_parent_id = self._get_or_create_folder(part, current_parent_id)
            return True
        except Exception as e:
            print(f"[GoogleDrive] [Error] 디렉토리 생성 실패 ({path}): {e}")
            return False

    def load_dataframe(self, path: str, sheet_name: str | None = None, **kwargs) -> pd.DataFrame:
        """구글 드라이브의 Excel 혹은 CSV 파일을 스트림으로 다운로드 직후 DataFrame 객체로 렌더링합니다.

        Args:
            path (str): 타겟 탐색 원격 파일의 닉네임 혹은 루트 기준 경로 이름.
            sheet_name (str | None): 시트 타겟 지시 객체. (csv면 제외)
            **kwargs: `pd.read_csv`, `pd.read_excel` 기능 스위치 추가 전달자.

        Returns:
            pd.DataFrame: 데이터 포함된 프레임, I/O나 기타 예외시 빈 생성 프레임 반환.
        """
        try:
            file_id = self._get_file_id(path)
            if not file_id:
                return pd.DataFrame()

            request = self.drive_service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()

            fh.seek(0)

            if path.lower().endswith(".csv"):
                return pd.read_csv(fh, **kwargs)
            else:
                target_sheet = 0 if sheet_name is None else sheet_name
                return pd.read_excel(fh, sheet_name=target_sheet, **kwargs)
        except Exception as e:
            print(f"[GoogleDrive] [Error] DataFrame 로드 실패 ({path}): {e}")
            return pd.DataFrame()

    def load_parquet(self, path: str, **kwargs) -> pd.DataFrame:
        """Google Drive 서버의 타겟 위치에서 Parquet 파일을 내려받아 DataFrame으로 변환합니다.

        Args:
            path (str): 클라우드 폴더 상의 .parquet 파일경로.
            **kwargs: 읽어오는데 쓰일 인자.

        Returns:
            pd.DataFrame: 원격 Parquet 데이터의 판다스 프레임.
        """
        try:
            file_id = self._get_file_id(path)
            if not file_id:
                return pd.DataFrame()

            request = self.drive_service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()

            fh.seek(0)
            return pd.read_parquet(fh, engine="pyarrow", **kwargs)
        except Exception as e:
            print(f"[GoogleDrive] [Error] Parquet 로드 실패 ({path}): {e}")
            return pd.DataFrame()

    def get_file(self, path: str) -> bytes | None:
        """API 연동을 통해 단일 원격 파일 요소의 전체 바이트를 시스템 상으로 동기 다운로드합니다.

        Args:
            path (str): 단일 파일 클라우드 내 지시 위치 문자열.

        Returns:
            bytes | None: 파일 내부 contents가 반환되며, 소진되거나 에러시 원소 None.
        """
        try:
            file_id = self._get_file_id(path)
            if not file_id:
                return None

            request = self.drive_service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()

            fh.seek(0)
            return fh.read()
        except Exception as e:
            print(f"[GoogleDrive] [Error] 파일 다운로드 실패 ({path}): {e}")
            return None

    def put_file(self, path: str, data: bytes) -> bool:
        """로컬 메모리의 바이트 콘텐츠를 Google Drive 특정 위치에 새로운 단일 파일로 업로드 작성합니다.

        Args:
            path (str): 드라이브 상에서 파일 이름 확장자까지 포함된 절대 도달 경로식 이름.
            data (bytes): 원시 이진 버퍼.

        Returns:
            bool: 청크 기록 완전 이행 성공 시 True 제공.
        """
        try:
            if path.endswith(".xlsx"):
                mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif path.endswith(".csv"):
                mime_type = "text/csv"
            else:
                mime_type = "application/octet-stream"

            output = io.BytesIO(data)
            self._upload_file(output, path, mime_type)
            print(f"[GoogleDrive] [OK] 파일 업로드: {path}")
            return True
        except Exception as e:
            print(f"[GoogleDrive] [Error] 파일 업로드 실패 ({path}): {e}")
            return False
