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
    """Google Drive 저장소 Adapter.

    StoragePort를 구현하여 Google Drive에 데이터를 저장하고 로드합니다.
    OAuth 2.0 Token을 사용하여 인증합니다.
    """

    SCOPES = ["https://www.googleapis.com/auth/drive"]

    def __init__(
        self,
        token_file: str,
        root_folder_name: str = "KRX_Auto_Crawling_Data",
        root_folder_id: str | None = None,
        client_secret_file: str | None = None,
    ):
        """GoogleDriveAdapter 초기화."""
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
        """DataFrame을 Excel 파일로 저장 (업로드)."""
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
        """DataFrame을 CSV 파일로 저장 (업로드)."""
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
        """DataFrame을 Parquet 파일로 저장 (업로드)."""
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
        """openpyxl Workbook 저장 (업로드)."""
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
        """Excel Workbook 로드 (다운로드)."""
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
        """Excel/CSV 파일에서 DataFrame을 로드 (다운로드)."""
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
        """Parquet 파일에서 DataFrame을 로드 (다운로드)."""
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
        """파일의 내용을 바이트로 읽어옵니다 (다운로드)."""
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
        """바이트 데이터를 파일로 저장합니다 (업로드)."""
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
