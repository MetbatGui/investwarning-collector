"""cmd_year()가 실패한 연도가 있으면 0이 아닌 exit code를 반환하는지 검증
(docker_guide.md §10 - 예외를 삼키고 exit 0으로 끝나는 문제).
"""

import argparse

import cli
from investment_hub.application.services import WarningCollectionService


def _make_args(**overrides):
    defaults = dict(
        year=None,
        start=2026,
        end=2026,
        end_date=None,
        include_active=True,
        storage="local",
        output_dir="tests/dummy_out_cmdyear",
        token_file="secrets/token.json",
        client_secret="secrets/client_secret.json",
        drive_folder="KRX_Auto_Crawling_Data",
        drive_folder_id=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_cmd_year_returns_nonzero_when_all_years_fail(monkeypatch):
    monkeypatch.setattr(WarningCollectionService, "collect_year", lambda self, *a, **k: False)

    exit_code = cli.cmd_year(_make_args())

    assert exit_code == 1


def test_cmd_year_returns_zero_when_all_years_succeed(monkeypatch):
    monkeypatch.setattr(WarningCollectionService, "collect_year", lambda self, *a, **k: True)

    exit_code = cli.cmd_year(_make_args())

    assert exit_code == 0


def test_cmd_year_returns_nonzero_when_some_years_fail(monkeypatch):
    calls = {"n": 0}

    def _flaky(self, *a, **k):
        calls["n"] += 1
        return calls["n"] > 1  # 첫 연도만 실패, 나머지는 성공

    monkeypatch.setattr(WarningCollectionService, "collect_year", _flaky)

    exit_code = cli.cmd_year(_make_args(start=2025, end=2026))

    assert exit_code == 1
