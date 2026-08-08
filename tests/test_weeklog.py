"""按周日志切分单元测试。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from futu_ai_quant.utils.weeklog import (
    iso_week_key,
    parse_line_timestamp,
    split_log_file,
    week_log_path,
)


def test_iso_week_key() -> None:
    assert iso_week_key(datetime(2026, 8, 3)) == "2026-W32"
    assert iso_week_key(datetime(2026, 8, 9)) == "2026-W32"
    assert iso_week_key(datetime(2026, 8, 10)) == "2026-W33"


def test_week_log_path(tmp_path: Path) -> None:
    p = week_log_path("watchlist", datetime(2026, 8, 5), log_dir=tmp_path)
    assert p.name == "watchlist_2026-W32.log"


def test_parse_line_timestamp() -> None:
    ts = parse_line_timestamp("[2026-08-05 13:05:00] [指标] hello")
    assert ts == datetime(2026, 8, 5, 13, 5, 0)
    assert parse_line_timestamp("no ts") is None


def test_infer_service_name() -> None:
    from futu_ai_quant.utils.weeklog import infer_service_name

    assert infer_service_name(Path("watchlist.log")) == "watchlist"
    assert infer_service_name(Path("watchlist.log.pre_weekly.bak")) == "watchlist"
    assert infer_service_name(Path("intraday_2026-W32.log")) == "intraday"


def test_split_log_file_by_week(tmp_path: Path) -> None:
    src = tmp_path / "watchlist.log"
    src.write_text(
        "\n".join(
            [
                "[2026-08-03 09:00:00] [循环] week32 a",
                "[2026-08-07 15:45:00] [循环] week32 b",
                "[2026-08-10 09:00:00] [循环] week33 c",
                "continuation without ts",
                "",
            ]
        ),
        encoding="utf-8",
    )
    counts = split_log_file(src, log_dir=tmp_path)
    assert counts["2026-W32"] == 2
    assert counts["2026-W33"] == 2  # line + continuation
    w32 = (tmp_path / "watchlist_2026-W32.log").read_text(encoding="utf-8")
    w33 = (tmp_path / "watchlist_2026-W33.log").read_text(encoding="utf-8")
    assert "week32 a" in w32 and "week33 c" in w33
    assert "continuation" in w33
