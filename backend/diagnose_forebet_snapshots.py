from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from app.services.forebet import _parse_float, _parse_score, _text, parse_forebet_html


def diagnose(path: Path, requested_date: date) -> dict:
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(".schema > .rcnt")
    parsed = parse_forebet_html(html, path.as_uri())
    same_date = [m for m in parsed if (m.kickoff.date() if hasattr(m.kickoff, "date") else m.kickoff) == requested_date]
    draws = [m for m in same_date if m.predicted_result.value == "DRAW"]
    return {
        "file": str(path), "requested_date": requested_date.isoformat(),
        "raw_fixture_rows": len(rows), "parsed_fixtures": len(parsed),
        "valid_kickoff": sum(m.kickoff is not None for m in parsed),
        "same_date_fixtures": len(same_date),
        "off_date_fixtures": len(parsed) - len(same_date),
        "draw_x": len(draws),
        "pred_1": sum(m.predicted_result.value == "HOME" for m in same_date),
        "pred_2": sum(m.predicted_result.value == "AWAY" for m in same_date),
        "unparsed_rows": len(rows) - len(parsed),
        "draw_candidates": [m.model_dump(mode="json") for m in draws],
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "snapshots"
    reports = [diagnose(root / f"forebet-{day}.html", date.fromisoformat(day)) for day in ("2026-08-25", "2026-08-26", "2026-08-27")]
    print(json.dumps(reports, indent=2, ensure_ascii=False, default=str))
