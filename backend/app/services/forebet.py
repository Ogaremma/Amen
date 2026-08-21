from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.config.settings import get_settings
from app.schemas.forebet import (
    ForebetMatch,
    ForebetPredictionResult,
    ForebetProbability,
)


def _text(node) -> str | None:
    if node is None:
        return None
    value = " ".join(node.get_text(" ", strip=True).split())
    return value or None


def normalize_team_name(name: str) -> str:
    value = " ".join(name.strip().split()).casefold()
    return re.sub(r"\s*([,.;:!?()\[\]{}])\s*", r"\1", value)


def _parse_prediction_result(value: str | None) -> ForebetPredictionResult:
    return {"1": ForebetPredictionResult.HOME, "X": ForebetPredictionResult.DRAW, "2": ForebetPredictionResult.AWAY}.get(
        (value or "").strip().upper(), ForebetPredictionResult.UNKNOWN
    )


def _parse_score(value: str | None) -> tuple[int | None, int | None]:
    match = re.search(r"(\d+)\s*[-:]\s*(\d+)", value or "")
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _parse_float(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", value)
    return float(match.group().replace(",", ".")) if match else None


def _parse_competition(onclick: str | None) -> tuple[str | None, str | None]:
    if not onclick:
        return None, None
    args = re.search(r"getstag\s*\(\s*this\s*,\s*[^,]+,\s*(['\"])(.*?)\1\s*,\s*(['\"])(.*?)\3", onclick)
    return (args.group(4), args.group(2)) if args else (None, None)


def _parse_kickoff(value: str | None) -> date | datetime | None:
    if not value:
        return None
    if len(value) == 10:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None


def parse_forebet_html(html: str, source_url: str | None = None) -> list[ForebetMatch]:
    soup = BeautifulSoup(html, "html.parser")
    matches: list[ForebetMatch] = []
    for row in soup.select(".schema > .rcnt"):
        home = _text(row.select_one('.homeTeam [itemprop="name"]'))
        away = _text(row.select_one('.awayTeam [itemprop="name"]'))
        if not home or not away:
            continue
        event = row.select_one('[itemtype="http://schema.org/SportsEvent"]')
        match_id = row.select_one('.stcn .fav_icon[id]')
        league_img = row.select_one('.stcn img.flsc[onclick]')
        competition, country = _parse_competition(league_img.get("onclick") if league_img else None)
        probabilities = row.select(".fprc > span")
        probability_model = None
        if len(probabilities) >= 3:
            probability_model = ForebetProbability(
                home=_parse_float(_text(probabilities[0])), draw=_parse_float(_text(probabilities[1])), away=_parse_float(_text(probabilities[2]))
            )
        score_home, score_away = _parse_score(_text(row.select_one(".predict .scrmobpred")))
        odds = row.select(".prmod .haodd > span")
        match_href = row.select_one('a.tnmscn[itemprop="url"]')
        kickoff_node = row.select_one('time[itemprop="startDate"]')
        narrative_node = row.select_one(".prsb_det .prsb_tx")
        if narrative_node is None:
            sibling = row.find_next_sibling("div", class_="prsb_det")
            narrative_node = sibling.select_one(".prsb_tx") if sibling else None
        matches.append(ForebetMatch(
            match_id=match_id.get("id") if match_id else None,
            home_team=home, away_team=away, competition=competition, country=country,
            competition_code=_text(row.select_one(".shortTag")),
            kickoff=_parse_kickoff(kickoff_node.get("datetime") if kickoff_node else None),
            kickoff_display=_text(row.select_one(".date_bah")),
            match_url=urljoin(source_url or get_settings().forebet_base_url, match_href.get("href")) if match_href and match_href.get("href") else None,
            predicted_result=_parse_prediction_result(_text(row.select_one(".predict .forepr"))),
            predicted_score_home=score_home, predicted_score_away=score_away, probabilities=probability_model,
            average_goals=_parse_float(_text(row.select_one(".avg_sc.tabonly"))),
            primary_coefficient=_parse_float(_text(row.select_one(".prmod > .lscrsp"))),
            odds_home=_parse_float(_text(odds[0])) if len(odds) > 0 else None,
            odds_draw=_parse_float(_text(odds[1])) if len(odds) > 1 else None,
            odds_away=_parse_float(_text(odds[2])) if len(odds) > 2 else None,
            narrative=_text(narrative_node), source_url=source_url,
        ))
    return matches


def is_draw_prediction(prediction: ForebetPredictionResult) -> bool:
    return prediction == ForebetPredictionResult.DRAW


def get_draw_matches(matches: Iterable[ForebetMatch]) -> list[ForebetMatch]:
    return [match for match in matches if is_draw_prediction(match.predicted_result)]


async def fetch_forebet_page(url: str) -> str:
    settings = get_settings()
    headers = {"User-Agent": settings.forebet_user_agent}
    async with httpx.AsyncClient(timeout=settings.forebet_timeout, headers=headers, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text
