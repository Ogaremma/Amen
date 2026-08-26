from datetime import date, datetime
from pathlib import Path

from app.services.forebet import parse_forebet_html
from diagnose_forebet_snapshots import diagnose


ROOT = Path(__file__).resolve().parents[2] / "snapshots"


def test_snapshot_diagnostics_cover_every_raw_row_and_x_candidate():
    expected = {"2026-08-25": (44, 42, 11), "2026-08-26": (44, 42, 7), "2026-08-27": (20, 18, 3)}
    for day, counts in expected.items():
        report = diagnose(ROOT / f"forebet-{day}.html", date.fromisoformat(day))
        assert (report["raw_fixture_rows"], report["same_date_fixtures"], report["draw_x"]) == counts
        assert report["unparsed_rows"] == 0


def test_visible_time_is_preserved_and_date_only_fallback_remains_supported():
    html = (ROOT / "forebet-2026-08-26.html").read_text(encoding="utf-8")
    matches = parse_forebet_html(html)
    first = matches[0]
    assert first.kickoff == datetime(2026, 8, 26, 2, 30)


def test_x_is_draw_and_numeric_predictions_are_not_draw():
    html = '<div class="schema"><div class="rcnt"><span class="homeTeam"><span itemprop="name">A</span></span><span class="awayTeam"><span itemprop="name">B</span></span><time itemprop="startDate" datetime="2026-08-26"></time><div class="predict"><span class="forepr">X</span></div></div><div class="rcnt"><span class="homeTeam"><span itemprop="name">C</span></span><span class="awayTeam"><span itemprop="name">D</span></span><time itemprop="startDate" datetime="2026-08-26"></time><div class="predict"><span class="forepr">1</span></div></div></div>'
    matches = parse_forebet_html(html)
    assert [m.predicted_result.value for m in matches] == ["DRAW", "HOME"]
