"""SQLite 기반 투자경고종목 레포지터리 어댑터 구현 (DB SSOT).

db_ssot_guide.md 표준을 따른다:
- PK는 정정 불가능한 자연키 조합 (code, designation_date, date) - §2
- 쓰기는 SQL upsert(INSERT ... ON CONFLICT DO UPDATE)를 트랜잭션으로 묶음 - §5, §7
- 연도별로 하나의 SQLite 파일을 둔다(ParquetRepositoryAdapter와 동일한 파티션 관례 유지)::

    <base_dir>/
        2024.db
        2025.db
        ...
"""

import sqlite3
from pathlib import Path

from investment_hub.core.ports.repository_port import WarningStockRepository
from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock

_SCHEMA = """
CREATE TABLE IF NOT EXISTS warning_prices (
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    designation_date TEXT NOT NULL,
    release_date TEXT,
    date TEXT NOT NULL,
    close INTEGER NOT NULL,
    change_rate REAL NOT NULL,
    PRIMARY KEY (code, designation_date, date)
)
"""

_UPSERT = """
INSERT INTO warning_prices (
    code, name, market, designation_date, release_date, date, close, change_rate
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(code, designation_date, date) DO UPDATE SET
    name=excluded.name,
    market=excluded.market,
    release_date=excluded.release_date,
    close=excluded.close,
    change_rate=excluded.change_rate
"""

_DATE_FMT = "%Y-%m-%d"


class SqliteRepositoryAdapter(WarningStockRepository):
    """SQLite 파일을 백엔드로 사용하는 투자경고종목 레포지터리 (연도별 SSOT DB)."""

    def __init__(self, base_dir: str = "output/db") -> None:
        self._base_dir = Path(base_dir)

    # ------------------------------------------------------------------
    # 경로/연결 헬퍼
    # ------------------------------------------------------------------

    def _db_path(self, year: int) -> Path:
        return self._base_dir / f"{year}.db"

    def _connect(self, year: int) -> sqlite3.Connection:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self._db_path(year)))
        con.execute(_SCHEMA)
        return con

    # ------------------------------------------------------------------
    # 도메인 모델 <-> 행 변환
    # ------------------------------------------------------------------

    def _rows_from_domain(
        self, stocks: list[InvestmentWarningStock], prices: dict[str, list[DailyPriceData]]
    ) -> list[tuple]:
        rows = []
        for stock in stocks:
            release_str = stock.release_date.strftime(_DATE_FMT) if stock.release_date else None
            for dp in prices.get(stock.code, []):
                rows.append(
                    (
                        stock.code,
                        stock.name,
                        stock.market,
                        stock.designation_date.strftime(_DATE_FMT),
                        release_str,
                        dp.date.strftime(_DATE_FMT),
                        int(dp.close),
                        float(dp.change_rate),
                    )
                )
        return rows

    # ------------------------------------------------------------------
    # WarningStockRepository 구현
    # ------------------------------------------------------------------

    def save_year(
        self,
        year: int,
        stocks: list[InvestmentWarningStock],
        prices: dict[str, list[DailyPriceData]],
    ) -> None:
        """특정 연도 데이터를 전량 재작성합니다(기존 행 삭제 후 upsert, 한 트랜잭션)."""
        rows = self._rows_from_domain(stocks, prices)
        con = self._connect(year)
        try:
            con.execute("BEGIN")
            con.execute("DELETE FROM warning_prices")
            con.executemany(_UPSERT, rows)
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def load_year(
        self,
        year: int,
    ) -> tuple[list[InvestmentWarningStock], dict[str, list[DailyPriceData]]]:
        """특정 연도 데이터를 로드합니다. 파티션이 없으면 ([], {}) 반환합니다."""
        if not self._db_path(year).exists():
            return [], {}

        con = self._connect(year)
        try:
            rows = con.execute(
                "SELECT code, name, market, designation_date, release_date, date, close, change_rate "
                "FROM warning_prices ORDER BY designation_date, code, date"
            ).fetchall()
        finally:
            con.close()

        if not rows:
            return [], {}
        return self._restore_stocks(rows), self._restore_prices(rows)

    def _restore_stocks(self, rows: list[tuple]) -> list[InvestmentWarningStock]:
        seen: dict[tuple, InvestmentWarningStock] = {}
        for code, name, market, desig, release, _date, _close, _rate in rows:
            key = (code, desig)
            if key in seen:
                continue
            seen[key] = InvestmentWarningStock(
                code=code,
                name=name,
                market=market,
                designation_date=_parse_date(desig),
                release_date=_parse_date(release) if release else None,
            )
        return list(seen.values())

    def _restore_prices(self, rows: list[tuple]) -> dict[str, list[DailyPriceData]]:
        prices: dict[str, list[DailyPriceData]] = {}
        seen_dates: dict[str, set] = {}
        for code, name, _market, _desig, _release, date_str, close, rate in rows:
            seen = seen_dates.setdefault(code, set())
            if date_str in seen:
                continue
            seen.add(date_str)
            prices.setdefault(code, []).append(
                DailyPriceData(code=code, name=name, date=_parse_date(date_str), close=float(close), change_rate=float(rate))
            )
        for code in prices:
            prices[code].sort(key=lambda p: p.date)
        return prices

    def append_year(
        self,
        year: int,
        stocks: list[InvestmentWarningStock],
        prices: dict[str, list[DailyPriceData]],
    ) -> None:
        """기존 파티션에 새 데이터를 upsert 병합합니다.

        (code, designation_date, date) 키가 같으면 새 데이터로 덮어쓰고(release_date
        동기화 등), 없으면 새 행으로 추가합니다.
        """
        rows = self._rows_from_domain(stocks, prices)
        if not rows:
            return
        con = self._connect(year)
        try:
            con.execute("BEGIN")
            con.executemany(_UPSERT, rows)
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def year_exists(self, year: int) -> bool:
        return self._db_path(year).exists()

    def list_available_years(self) -> list[int]:
        if not self._base_dir.exists():
            return []
        years = []
        for f in self._base_dir.glob("*.db"):
            if f.stem.isdigit():
                years.append(int(f.stem))
        return sorted(years)


def _parse_date(date_str: str):
    from datetime import datetime

    return datetime.strptime(date_str, _DATE_FMT)
