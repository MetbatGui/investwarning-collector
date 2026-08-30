# ⚠️ Investment Warning Collector

KRX(한국거래소)가 지정하는 **투자경고종목**을 매일 수집해, 지정일~해제일(+N영업일)
구간의 일별 시세를 SQLite에 쌓고 연도별 엑셀 리포트로 만드는 배치 프로그램입니다.
생성된 리포트는 구글 드라이브에 자동 업로드됩니다.

이 문서는 다음 개발자 또는 시스템 관리자가 프로젝트를 빠르고 정확하게 파악하고 인수인계받을
수 있도록 작성되었습니다.

---

## ✨ 주요 기능

- **일일 자동 증분 수집** (`today`): 최근 N영업일(기본 7일) 동안의 신규/해제일 변경 종목을
  감지해 시세를 갱신합니다. 컨테이너 내장 cron이 평일 16:00 KST에 자동 실행합니다.
- **연도 백필** (`year`): 지정 연도의 투자경고종목 전체를 소급 수집합니다.
- **엑셀 리포트 재생성** (`export-excel`): DB를 다시 읽어 엑셀만 재렌더링합니다(재수집 없음).
- **해제일 거래정지 대응**: 해제일 당일이 거래정지(등락률 0)면 다음 영업일 시세를 기준으로
  해제전/해제직후 등락률을 계산합니다.
- **구글 드라이브 동기화**: `--storage drive`로 실행하면 엑셀 리포트를 Drive에 업로드합니다.

---

## 🏗 아키텍처

포트-어댑터(Hexagonal Architecture) 구조로, 비즈니스 로직이 외부 인프라(KRX, SQLite,
구글 드라이브)에 직접 의존하지 않습니다.

```
investwarning-collector/
├── docker/              # Docker 환경 구축 파일 (Dockerfile, docker-compose, cron 스크립트)
├── secrets/             # 인증 자격 증명 키 저장소 (Git 제외 대상)
│   ├── client_secret.json     # Google OAuth 클라이언트 보안 비밀
│   └── token.json              # 최초 실행 시 생성되는 OAuth 토큰
├── output/
│   └── db/               # SQLite SSOT DB, 연도별 {year}.db 분리 (Git 제외 대상)
├── cache/pykrx/          # KRX 응답 로컬 캐시 (티커/매핑/OHLCV, Git 제외 대상)
├── logs/                 # 날짜별 실행 로그 (Git 제외 대상)
├── src/investment_hub/
│   ├── domain/            # 순수 도메인 모델 (InvestmentWarningStock, DailyPriceData,
│   │                      # CollectionResult)
│   ├── core/ports/        # 어댑터용 인터페이스 (repository, storage)
│   ├── application/       # WarningCollectionService(수집 오케스트레이션), ReportGenerationService
│   └── infrastructure/
│       ├── scrapers/       # KRX KIND 투자경고종목 목록 스크래핑
│       ├── adapters/       # NativeKrxAdapter(KRX 직접 로그인), SqliteRepositoryAdapter(SSOT),
│       │                   # Local/GoogleDriveAdapter, KrxCalendarService(휴장일)
│       └── collectors/     # daily_price_collector (배치 시세 수집)
└── cli.py                # CLI 진입점 (today / year / export-excel)
```

- `WarningCollectionService.collect_today()`가 KRX 조회→필터→기존 데이터 동기화→누락 시세
  수집→저장→엑셀 export 전체 흐름을 소유하고, `CollectionResult` 값 객체(성공 여부, 신규
  종목/시세 건수)를 반환합니다(`orchestration_guide.md` §1, §2.2). CLI는 이 값을 로그/exit
  code로 변환만 합니다.
- `SqliteRepositoryAdapter`가 SSOT이며, `(code, designation_date, date)` 복합 키(정정
  불가능한 자연키 조합)로 SQL upsert합니다(`db_ssot_guide.md` §2, §5).
- `NativeKrxAdapter`가 KRX에 직접 로그인해 시세를 수집합니다 — pykrx 등 서드파티 스크래퍼에
  의존하지 않습니다(2026-04 KRX 접속 방식 변경으로 pykrx가 깨진 뒤 이 방식으로 전환).
  `data.krx.co.kr`로 나가는 모든 요청에 최소 1초 간격 레이트리밋이 걸려 있습니다 —
  풀지 마세요, 실제로 이 딜레이 없이 짧은 시간에 요청이 몰려 KRX IP 차단(24시간)을 맞은
  적이 있습니다.

---

## 🚀 환경 설정 및 설치

### 1. 사전 요구 사항
- **Python 3.12** 이상 및 **`uv`** 패키지 관리자
- KRX 정보데이터시스템(data.krx.co.kr) 회원 계정 (로그인 필수)
- **Docker 및 Docker Compose** (컨테이너 실행 시)

### 2. 패키지 설치
```bash
uv sync
```

### 3. 환경 변수 설정 (`.env`)
```env
KRX_USERNAME=your_krx_username
KRX_PASSWORD=your_krx_password

GOOGLE_DRIVE_ROOT_FOLDER_ID=your_google_drive_folder_id
```

### 4. 시크릿 설정
`secrets/client_secret.json`(Google Cloud Console에서 발급받은 OAuth 2.0 Desktop app 클라이언트)을
넣어두면, `--storage drive` 최초 실행 시 브라우저 인증을 거쳐 `secrets/token.json`이
자동 생성됩니다.

---

## 💻 사용법

```bash
# 최근 7일 증분 수집 (로컬 저장)
uv run python cli.py today --days 7

# 최근 30영업일 증분 수집 + 구글 드라이브 업로드 (cron이 실행하는 것과 동일)
uv run python cli.py today --days 30 --storage drive

# 특정 날짜 기준 수집
uv run python cli.py today --date 2026-08-28 --days 7

# 연도 백필
uv run python cli.py year --year 2024

# DB를 다시 읽어 엑셀만 재렌더링 (재수집 없음)
uv run python cli.py export-excel --year 2026
```

---

## 🐳 Docker로 실행

```bash
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml run --rm investwarning-collector python cli.py today --storage drive
docker compose -f docker/docker-compose.yml up -d investwarning-collector-cron
```

컨테이너 내장 cron이 스케줄에 따라 `today --days 30 --storage drive`를 자동 실행합니다
(최대 1달 공백까지 자동 백필).
스케줄은 `docker/crontab`을 참고하세요(기본: 평일 16:00 KST).

---

## 🧪 테스트

```bash
uv run pytest
```

---

## 💡 인수인계 시 주의 사항 (개발 팁)

1. **KRX 레이트리밋을 절대 건드리지 말 것**: `NativeKrxAdapter._throttle()`이 모든
   `data.krx.co.kr` 요청 사이에 최소 1초를 강제합니다. 이 딜레이 없이 짧은 시간에 요청이
   몰려 "비정상 대량 조회" 판정으로 KRX IP가 24시간 차단된 사고가 실제로 있었습니다
   (과거엔 유사한 사고로 2개월 차단까지 겪은 적도 있습니다). 새 수집 루프를 추가할 때
   반드시 이 어댑터를 거치도록 하세요.
2. **pykrx 사용 금지**: 2026-04 KRX 접속 방식 변경(로그인 필수화) 이후 pykrx는 KRX_ID/PW
   없이는 전부 `LOGOUT`을 받습니다. 이 프로젝트는 `NativeKrxAdapter`(직접 로그인)로 완전히
   대체했습니다 — pykrx를 다시 들여오지 마세요.
3. **SQLite가 SSOT**: `output/db/{year}.db`. Parquet(`ParquetRepositoryAdapter`)은 과거
   구현으로 남아있지만 더 이상 실사용 경로에 연결돼 있지 않습니다.
4. **`(code, designation_date, date)`가 PK**: 같은 종목이 여러 번 투자경고 지정될 수 있어
   `designation_date`까지 키에 포함합니다. `release_date`는 정정될 수 있어 PK에서 제외했습니다.
5. **의존성 패키지 관리 (`uv`)**: `pip install` 대신 `uv add <패키지명>`을 사용해
   `pyproject.toml`/`uv.lock`을 자동 최신화하세요.
