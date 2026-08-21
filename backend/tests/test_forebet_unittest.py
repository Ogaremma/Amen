from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.forebet import ForebetMatch, ForebetPredictionResult
from app.services.forebet import (
    fetch_forebet_page,
    get_draw_matches,
    is_draw_prediction,
    normalize_team_name,
    parse_forebet_html,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
FIXTURE = next(
    path
    for path in (
        REPOSITORY_ROOT / "forebet_sample.html",
        REPOSITORY_ROOT / "forebet_sampls.html",
    )
    if path.exists()
)
SOURCE_URL = "https://www.forebet.com/en/football-tips-and-predictions-for-tomorrow"


@pytest.fixture(scope="module")
def sample_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def matches(sample_html: str) -> list[ForebetMatch]:
    return parse_forebet_html(sample_html, SOURCE_URL)


def test_sample_has_44_match_rows(sample_html: str):
    assert len(BeautifulSoup(sample_html, "html.parser").select(".schema > .rcnt")) == 44


def test_parser_returns_44_valid_matches(matches):
    assert len(matches) == 44


def test_first_match_fields(matches):
    match = matches[0]
    assert match.home_team == "Venados Yucatán"
    assert match.away_team == "Dorados Sinaloa"
    assert match.match_id == "2475110"
    assert match.match_url == "https://www.forebet.com/en/football/matches/venados-yucatán-dorados-sinaloa-2475110"
    assert match.competition == "Liga de Expansion MX"
    assert match.country == "Mexico"
    assert match.competition_code == "Mx2"
    assert str(match.kickoff) == "2026-08-21"
    assert match.kickoff_display == "21/08/2026 0:00"
    assert match.predicted_result == ForebetPredictionResult.DRAW
    assert (match.predicted_score_home, match.predicted_score_away) == (2, 2)
    assert match.probabilities.model_dump() == {"home": 35.0, "draw": 41.0, "away": 24.0}
    assert match.average_goals == 3.27
    assert match.primary_coefficient == 4.33
    assert (match.odds_home, match.odds_draw, match.odds_away) == (1.61, 4.33, 4.75)
    assert "won their last 4 home matches" in match.narrative


def test_home_and_away_prediction_conversion(matches):
    assert any(match.predicted_result == ForebetPredictionResult.HOME for match in matches)
    assert any(match.predicted_result == ForebetPredictionResult.AWAY for match in matches)


@pytest.mark.parametrize("token,expected", [("1", "HOME"), ("X", "DRAW"), ("2", "AWAY"), ("?", "UNKNOWN")])
def test_prediction_tokens(token, expected):
    html = f'<div class="schema"><div class="rcnt"><div class="tnms"><span class="homeTeam"><span itemprop="name">A</span></span><span class="awayTeam"><span itemprop="name">B</span></span></div><div class="predict"><span class="forepr">{token}</span></div></div></div>'
    assert parse_forebet_html(html)[0].predicted_result.value == expected


def test_optional_fields_and_malformed_rows():
    html = '<div class="schema"><div class="rcnt"><span class="homeTeam"><span itemprop="name">A</span></span><span class="awayTeam"><span itemprop="name">B</span></span></div><div class="rcnt"><span class="homeTeam"></span></div></div>'
    parsed = parse_forebet_html(html)
    assert len(parsed) == 1
    assert parsed[0].probabilities is None
    assert parsed[0].predicted_score_home is None
    assert parsed[0].competition is None


def test_relative_and_absolute_urls():
    html = '<div class="schema"><div class="rcnt"><span class="homeTeam"><span itemprop="name">A</span></span><span class="awayTeam"><span itemprop="name">B</span></span><a class="tnmscn" itemprop="url" href="/match/1"></a></div><div class="rcnt"><span class="homeTeam"><span itemprop="name">C</span></span><span class="awayTeam"><span itemprop="name">D</span></span><a class="tnmscn" itemprop="url" href="https://example.com/match/2"></a></div></div>'
    parsed = parse_forebet_html(html, SOURCE_URL)
    assert parsed[0].match_url == "https://www.forebet.com/match/1"
    assert parsed[1].match_url == "https://example.com/match/2"


@pytest.mark.parametrize("name,expected", [
    ("  Manchester   United ", "manchester united"),
    ("ARSENAL", "arsenal"),
    ("Paris  Saint-Germain ", "paris saint-germain"),
    ("Team (Women)", "team(women)"),
])
def test_normalize_team_name(name, expected):
    assert normalize_team_name(name) == expected


def test_draw_helpers(matches):
    assert is_draw_prediction(ForebetPredictionResult.DRAW)
    assert not is_draw_prediction(ForebetPredictionResult.HOME)
    draws = get_draw_matches(matches)
    assert draws
    assert all(match.predicted_result == ForebetPredictionResult.DRAW for match in draws)


def test_network_failure():
    request = httpx.Request("GET", SOURCE_URL)
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=httpx.ConnectError("failed", request=request))):
        with pytest.raises(httpx.ConnectError):
            asyncio.run(fetch_forebet_page(SOURCE_URL))


def test_http_timeout():
    request = httpx.Request("GET", SOURCE_URL)
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=httpx.ReadTimeout("timeout", request=request))):
        with pytest.raises(httpx.ReadTimeout):
            asyncio.run(fetch_forebet_page(SOURCE_URL))


def test_api_rejects_non_forebet_url():
    response = TestClient(app).post("/api/v1/forebet/analyze", json={"url": "http://127.0.0.1/internal"})
    assert response.status_code == 422


def test_api_response(sample_html):
    with patch("app.api.forebet.fetch_forebet_page", new=AsyncMock(return_value=sample_html)):
        response = TestClient(app).post("/api/v1/forebet/analyze", json={"url": SOURCE_URL})
    assert response.status_code == 200
    body = response.json()
    assert body["total_matches"] == 44
    assert body["draw_count"] == len(body["draw_matches"])
