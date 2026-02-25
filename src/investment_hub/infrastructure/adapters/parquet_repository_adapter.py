"""Parquet 기반 투자경고종목 레포지터리 어댑터 구현."""

import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from investment_hub.core.ports.repository_port import WarningStockRepository
from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock

# Parquet 스키마 컬럼 정의
_SCHEMA_COLUMNS = [
    "year",
    "code",
    "name",
    "market",
    "designation_date",
    "release_date",
    "date",
    "close",
    "change_rate",
]

# Parquet 컬럼 dtype 매핑 (Arrow 엔진 최적화)
_DTYPE_MAP: dict[str, str] = {
    "year": "int16",
    "code": "str",
    "name": "str",
    "market": "str",
    "close": "int32",
    "change_rate": "float32",
}


class ParquetRepositoryAdapter(WarningStockRepository):
    """Parquet 파일을 백엔드로 사용하는 투자경고종목 레포지터리.

    연도별로 하나의 Parquet 파일을 생성합니다::

        <base_dir>/
            2024.parquet
            2025.parquet
            ...

    각 파일은 **Long-format** 구조를 사용합니다.
    (1행 = 투자경고 종목 1개 × 날짜 1개)

    원자적 저장을 보장하기 위해 임시 파일(``.tmp.parquet``)에
    먼저 쓴 뒤 원본 파일로 교체(rename)하는 방식을 사용합니다.

    Args:
        base_dir: Parquet 파일을 저장할 기본 디렉토리.
            기본값은 ``output/parquet``.

    Example:
        >>> repo = ParquetRepositoryAdapter("output/parquet")
        >>> repo.save_year(2025, stocks, prices)
        >>> stocks, prices = repo.load_year(2025)
    """

    def __init__(self, base_dir: str = "output/parquet") -> None:
        """ParquetRepositoryAdapter 초기화.

        Args:
            base_dir: Parquet 파일 저장 경로.
        """
        self._base_dir = Path(base_dir)

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def save_year(
        self,
        year: int,
        stocks: list[InvestmentWarningStock],
        prices: dict[str, list[DailyPriceData]],
    ) -> None:
        """특정 연도 데이터를 Parquet 파일로 저장합니다 (전량 덮어쓰기).

        기존 파티션이 있으면 완전히 대체합니다. 원자적 저장으로
        저장 도중 장애 발생 시에도 기존 파일이 보존됩니다.

        Args:
            year: 저장할 연도 (예: 2025).
            stocks: 투자경고 종목 목록.
            prices: 종목코드 → 일별 시세 목록 매핑.

        Raises:
            IOError: 디렉토리 생성 실패 또는 파일 쓰기 오류 발생 시.
        """
        df = self._to_dataframe(year, stocks, prices)
        self._write_parquet_atomic(year, df)

    def load_year(
        self,
        year: int,
    ) -> tuple[list[InvestmentWarningStock], dict[str, list[DailyPriceData]]]:
        """특정 연도 데이터를 Parquet 파일에서 로드합니다.

        Args:
            year: 조회할 연도.

        Returns:
            (stocks, prices) 튜플.
            - stocks: 지정일 오름차순으로 정렬된 종목 목록.
            - prices: 종목코드 → 날짜 오름차순 시세 목록 매핑.
            파티션이 없으면 ``([], {})`` 반환.
        """
        path = self._parquet_path(year)
        if not path.exists():
            return [], {}
        df = pd.read_parquet(str(path))
        return self._from_dataframe(df)

    def append_year(
        self,
        year: int,
        stocks: list[InvestmentWarningStock],
        prices: dict[str, list[DailyPriceData]],
    ) -> None:
        """기존 파티션에 새 데이터를 증분 병합합니다.

        중복 제거 기준: ``(code, designation_date, date)`` 복합 키.
        동일 키 충돌 시 **새 데이터를 우선** 적용합니다
        (release_date 변경 동기화 등에 활용).

        파티션이 없으면 ``save_year()`` 와 동일하게 신규 저장합니다.

        Args:
            year: 대상 연도.
            stocks: 추가할 종목 목록.
            prices: 추가할 종목코드 → 일별 시세 목록 매핑.

        Raises:
            IOError: 파일 쓰기 오류 발생 시.
        """
        new_df = self._to_dataframe(year, stocks, prices)

        path = self._parquet_path(year)
        if path.exists():
            existing_df = pd.read_parquet(str(path))
            # 새 데이터를 먼저 concat → 중복 시 새 데이터 우선 (keep='first')
            combined = pd.concat([new_df, existing_df], ignore_index=True)
            dedup_keys = ["code", "designation_date", "date"]
            combined = combined.drop_duplicates(subset=dedup_keys, keep="first")
            combined = combined.sort_values(
                ["designation_date", "code", "date"]
            ).reset_index(drop=True)
            self._write_parquet_atomic(year, combined)
        else:
            self._write_parquet_atomic(year, new_df)

    def year_exists(self, year: int) -> bool:
        """해당 연도의 Parquet 파티션이 존재하는지 확인합니다.

        Args:
            year: 확인할 연도.

        Returns:
            파티션 파일이 존재하면 ``True``.
        """
        return self._parquet_path(year).exists()

    def list_available_years(self) -> list[int]:
        """저장된 파티션 연도 목록을 오름차순으로 반환합니다.

        Returns:
            사용 가능한 연도 리스트 (예: ``[2020, 2021, 2025]``).
            저장 디렉토리가 없으면 ``[]`` 반환.
        """
        if not self._base_dir.exists():
            return []
        years = []
        for f in self._base_dir.glob("*.parquet"):
            # 임시 파일(.tmp.parquet) 제외
            if ".tmp." in f.name:
                continue
            stem = f.stem
            if stem.isdigit():
                years.append(int(stem))
        return sorted(years)

    # ─────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────

    def _parquet_path(self, year: int) -> Path:
        """연도에 해당하는 Parquet 파일 경로를 반환합니다.

        Args:
            year: 대상 연도.

        Returns:
            Parquet 파일의 절대/상대 Path 객체.
        """
        return self._base_dir / f"{year}.parquet"

    def _to_dataframe(
        self,
        year: int,
        stocks: list[InvestmentWarningStock],
        prices: dict[str, list[DailyPriceData]],
    ) -> pd.DataFrame:
        """종목·시세 데이터를 Long-format DataFrame으로 변환합니다.

        Args:
            year: 수집 연도.
            stocks: 투자경고 종목 목록.
            prices: 종목코드 → 일별 시세 매핑.

        Returns:
            스키마를 준수하는 Long-format DataFrame.
            시세가 없는 종목은 제외됩니다.
        """
        records = []
        # 빠른 조회를 위해 (code, designation_date) → stock 매핑
        stock_map = {(s.code, s.designation_date): s for s in stocks}

        for stock in stocks:
            code = stock.code
            stock_prices = prices.get(code, [])
            for dp in stock_prices:
                records.append(
                    {
                        "year": year,
                        "code": code,
                        "name": stock.name,
                        "market": stock.market,
                        "designation_date": stock.designation_date,
                        "release_date": stock.release_date,  # None 허용
                        "date": dp.date,
                        "close": dp.close,
                        "change_rate": dp.change_rate,
                    }
                )

        _ = stock_map  # 현재 미사용, 향후 확장용

        if not records:
            # 빈 DataFrame — 스키마 컬럼은 유지
            return pd.DataFrame(columns=_SCHEMA_COLUMNS)

        df = pd.DataFrame(records)

        # 타입 최적화
        for col, dtype in _DTYPE_MAP.items():
            if col in df.columns:
                df[col] = df[col].astype(dtype)

        # datetime 컬럼 타입 보장
        df["designation_date"] = pd.to_datetime(df["designation_date"])
        df["release_date"] = pd.to_datetime(df["release_date"])  # NaT 허용
        df["date"] = pd.to_datetime(df["date"])

        return df.sort_values(["designation_date", "code", "date"]).reset_index(drop=True)

    def _from_dataframe(
        self,
        df: pd.DataFrame,
    ) -> tuple[list[InvestmentWarningStock], dict[str, list[DailyPriceData]]]:
        """Long-format DataFrame을 도메인 객체로 복원합니다.

        Args:
            df: load_year 에서 읽은 Long-format DataFrame.

        Returns:
            (stocks, prices) 튜플. stocks는 지정일 오름차순 정렬.
        """
        if df.empty:
            return [], {}

        # ── 종목 메타 복원 ──────────────────────────────────────────────
        meta_cols = ["code", "name", "market", "designation_date", "release_date"]
        meta = (
            df[meta_cols]
            .drop_duplicates(subset=["code", "designation_date"])
            .sort_values("designation_date")
            .reset_index(drop=True)
        )

        stocks: list[InvestmentWarningStock] = []
        for _, row in meta.iterrows():
            release_raw = row["release_date"]
            release_dt: datetime | None = (
                None if pd.isna(release_raw) else pd.Timestamp(release_raw).to_pydatetime()
            )
            stocks.append(
                InvestmentWarningStock(
                    code=str(row["code"]),
                    name=str(row["name"]),
                    market=str(row["market"]),
                    designation_date=pd.Timestamp(row["designation_date"]).to_pydatetime(),
                    release_date=release_dt,
                )
            )

        # ── 시세 복원 ──────────────────────────────────────────────────
        prices: dict[str, list[DailyPriceData]] = {}
        for code, group in df.groupby("code"):
            code_str = str(code)
            price_list = []
            for _, row in group.sort_values("date").iterrows():
                price_list.append(
                    DailyPriceData(
                        code=code_str,
                        name=str(row["name"]),
                        date=pd.Timestamp(row["date"]).to_pydatetime(),
                        close=float(row["close"]),
                        change_rate=float(row["change_rate"]),
                    )
                )
            prices[code_str] = price_list

        return stocks, prices

    def _write_parquet_atomic(self, year: int, df: pd.DataFrame) -> None:
        """DataFrame을 원자적으로 Parquet 파일에 저장합니다.

        임시 파일(``.tmp.parquet``)에 먼저 기록한 뒤
        원본 파일로 교체(replace)하여 저장 중 장애로 인한
        데이터 손상을 방지합니다.

        Args:
            year: 저장 연도 (파일명 결정에 사용).
            df: 저장할 Long-format DataFrame.

        Raises:
            IOError: 디렉토리 생성 또는 파일 rename 실패 시.
        """
        self._base_dir.mkdir(parents=True, exist_ok=True)
        target = self._parquet_path(year)
        tmp = target.with_suffix(".tmp.parquet")

        try:
            df.to_parquet(str(tmp), index=False, engine="pyarrow")
            os.replace(str(tmp), str(target))
        except Exception:
            # 임시 파일 정리
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise
