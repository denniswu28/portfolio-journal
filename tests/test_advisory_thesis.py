"""Tests for the boist thesis loader (latest-on-or-before selection, extraction)."""

from datetime import date

from src.advisory.thesis import build_thesis_context, find_latest_thesis


def _write(dir_path, name, text):
    (dir_path / name).write_text(text, encoding="utf-8")


def test_find_latest_picks_on_or_before(tmp_path):
    _write(tmp_path, "boist-2026-05-31.md", "# Older\n")
    _write(tmp_path, "boist-2026-06-07.md", "# Newer\n")
    _write(tmp_path, "boist-2026-06-14.md", "# Future\n")
    picked = find_latest_thesis(date(2026, 6, 7), data_dir=tmp_path)
    assert picked is not None and picked.name == "boist-2026-06-07.md"


def test_find_latest_skips_future_only(tmp_path):
    _write(tmp_path, "boist-2026-06-14.md", "# Future\n")
    assert find_latest_thesis(date(2026, 6, 7), data_dir=tmp_path) is None


def test_build_context_extracts_title_and_tickers(tmp_path):
    _write(tmp_path, "boist-2026-06-07.md",
           "# Memory Supercycle Update\n\n"
           "The shortage in DRAM and NAND extends to 2030. SNDK support at 1200; "
           "MU rebound resistance near 1000. AVGO chips only.\n\n"
           "## Trading Plan\n- Sell SNDK call spreads\n- Hold memory shares\n")
    ctx = build_thesis_context(date(2026, 6, 7), data_dir=tmp_path)
    assert ctx.found is True
    assert ctx.title == "Memory Supercycle Update"
    assert "SNDK" in ctx.tickers and "MU" in ctx.tickers and "AVGO" in ctx.tickers
    assert "DRAM" not in ctx.tickers  # stopworded
    assert "Trading Plan" in ctx.digest


def test_known_ticker_filter(tmp_path):
    _write(tmp_path, "boist-2026-06-07.md", "# T\nBuy SNDK and ZZZZ today.\n")
    ctx = build_thesis_context(date(2026, 6, 7), data_dir=tmp_path, known_tickers={"SNDK"})
    assert ctx.tickers == ["SNDK"]


def test_missing_thesis_is_graceful(tmp_path):
    ctx = build_thesis_context(date(2026, 6, 7), data_dir=tmp_path)
    assert ctx.found is False and ctx.tickers == []


def test_staleness_flag(tmp_path):
    _write(tmp_path, "boist-2026-05-31.md", "# Older thesis\nContent.\n")
    ctx = build_thesis_context(date(2026, 6, 7), data_dir=tmp_path,
                               snapshot_date=date(2026, 6, 5))
    assert ctx.stale_vs_snapshot is True


def test_title_strips_bold_markdown(tmp_path):
    _write(tmp_path, "boist-2026-06-07.md", "**Bold headline title**\n\nBody.\n")
    ctx = build_thesis_context(date(2026, 6, 7), data_dir=tmp_path)
    assert ctx.title == "Bold headline title"


def test_explicit_file_override(tmp_path):
    _write(tmp_path, "boist-2026-05-31.md", "# Older\n")
    target = tmp_path / "boist-2026-06-07.md"
    target.write_text("# Chosen\nBody.\n", encoding="utf-8")
    ctx = build_thesis_context(date(2026, 6, 1), data_dir=tmp_path, explicit_file=target)
    assert ctx.title == "Chosen"
