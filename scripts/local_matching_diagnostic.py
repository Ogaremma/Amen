"""Local-only Forebet/SportyBet matching investigation. No writes or bookings."""
import asyncio, json
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from app.services.forebet import parse_forebet_html, get_draw_matches
from app.services.forebet_dates import future_prediction_dates, future_prediction_urls
from app.services.fixture_matching import match_forebet_fixtures, normalize_team_name
from app.services.sportybet import get_upcoming_football_events

LAGOS = timezone(timedelta(hours=1), name="Africa/Lagos")
SUFFIXES = {"fc", "cf", "sc", "afc", "cd", "c d", "club"}

def compact(name):
    tokens = normalize_team_name(name).split()
    return " ".join(t for t in tokens if t not in SUFFIXES)

def score(a, b):
    na, nb = normalize_team_name(a), normalize_team_name(b)
    ca, cb = compact(a), compact(b)
    token = len(set(ca.split()) & set(cb.split())) / max(len(set(ca.split()) | set(cb.split())), 1)
    return max(SequenceMatcher(None, na, nb).ratio(), SequenceMatcher(None, ca, cb).ratio(), token)

def competition_score(a, b):
    na, nb = normalize_team_name(a or ""), normalize_team_name(b or "")
    if not na or not nb:
        return 0.0
    at, bt = set(na.split()), set(nb.split())
    return max(SequenceMatcher(None, na, nb).ratio(), len(at & bt) / max(len(at | bt), 1))

def kickoff_delta_hours(forebet, event):
    if isinstance(forebet.kickoff, datetime):
        left = forebet.kickoff if forebet.kickoff.tzinfo else forebet.kickoff.replace(tzinfo=timezone.utc)
        return abs((left.astimezone(timezone.utc) - event.kickoff.astimezone(timezone.utc)).total_seconds()) / 3600
    local_midnight = datetime.combine(forebet.kickoff, datetime.min.time(), tzinfo=LAGOS)
    return abs((local_midnight.astimezone(timezone.utc) - event.kickoff.astimezone(timezone.utc)).total_seconds()) / 3600

def date_only_match(forebet, event):
    if isinstance(forebet.kickoff, datetime):
        return True
    return event.kickoff.astimezone(LAGOS).date() == forebet.kickoff

async def main():
    pages = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False)
        try:
            for day, url in zip(future_prediction_dates(), future_prediction_urls()):
                context = await browser.new_context(locale="en-US", viewport={"width": 1366, "height": 768})
                page = await context.new_page()
                response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
                html = await page.content()
                await page.wait_for_selector(".schema", timeout=60000)
                parsed = parse_forebet_html(html, url)
                pages.extend(get_draw_matches(parsed))
                print(json.dumps({"forebet_date": day.isoformat(), "status": response.status if response else None, "host": urlparse(page.url).hostname, "matches": len(parsed), "draws": len(get_draw_matches(parsed))}))
                await context.close()
        finally:
            await browser.close()
    catalogue = await get_upcoming_football_events()
    events = catalogue.events
    current = match_forebet_fixtures(pages, events)
    unmatched = [(f, r) for f, r in zip(pages, current) if r.status.value == "UNMATCHED"]
    report = {"sportybet_events": len(events), "forebet_draws": len(pages), "current_unmatched": len(unmatched), "candidate_evidence_report": [], "new_evidence_matches": [], "new_advanced_matches": [], "categories": {"A_strong_currently_rejected": [], "B_genuine_absence": [], "C_ambiguous": []}}
    for forebet, result in zip(pages, current):
        if result.matching_method == "evidence" and result.sportybet_event:
            event = result.sportybet_event
            report["new_evidence_matches"].append({"forebet_home": forebet.home_team, "forebet_away": forebet.away_team, "draw_probability": forebet.probabilities.draw if forebet.probabilities else None, "prediction_date": str(forebet.kickoff.date() if isinstance(forebet.kickoff, datetime) else forebet.kickoff), "sportybet_event_id": event.event_id, "sportybet_home": event.home_team, "sportybet_away": event.away_team, "sportybet_kickoff": event.kickoff.isoformat(), "competition": event.competition, "confidence": result.matching_confidence, "method": result.matching_method})
        if result.matching_method == "advanced_evidence" and result.sportybet_event:
            event = result.sportybet_event
            report["new_advanced_matches"].append({"forebet_home": forebet.home_team, "forebet_away": forebet.away_team, "sportybet_home": event.home_team, "sportybet_away": event.away_team, "sportybet_event_id": event.event_id, "matching_method": result.matching_method, "confidence": result.matching_confidence, "home_similarity": result.home_similarity, "away_similarity": result.away_similarity, "minimum_team_similarity": result.minimum_team_similarity, "average_team_similarity": result.average_team_similarity, "competition_similarity": result.competition_similarity, "kickoff_delta_hours": result.kickoff_delta_hours, "same_lagos_date": result.same_lagos_date, "same_direction": result.same_direction, "candidate_margin": result.candidate_margin})
    for forebet, result in unmatched:
        prediction_date = forebet.kickoff.date() if isinstance(forebet.kickoff, datetime) else forebet.kickoff
        candidates = []
        for event in events:
            home_score, away_score = score(forebet.home_team, event.home_team), score(forebet.away_team, event.away_team)
            same_date = date_only_match(forebet, event)
            comp_score = competition_score(forebet.competition, event.competition)
            delta = kickoff_delta_hours(forebet, event)
            combined = home_score * .4 + away_score * .4 + (1.0 if same_date else 0.0) * .15 + comp_score * .05
            candidates.append({"sportybet_home": event.home_team, "sportybet_away": event.away_team, "event_id": event.event_id, "kickoff": event.kickoff.isoformat(), "sportybet_competition": event.competition, "home_similarity": round(home_score, 3), "away_similarity": round(away_score, 3), "same_direction": True, "same_lagos_date": same_date, "kickoff_delta_hours": round(delta, 2), "competition_similarity": round(comp_score, 3), "evidence_score": round(combined, 3)})
        candidates.sort(key=lambda c: (c["evidence_score"], c["same_lagos_date"], -c["kickoff_delta_hours"]), reverse=True)
        candidates = candidates[:5]
        report["candidate_evidence_report"].append({"forebet_home": forebet.home_team, "forebet_away": forebet.away_team, "forebet_competition": forebet.competition, "draw_probability": forebet.probabilities.draw if forebet.probabilities else None, "prediction_date": str(prediction_date), "candidates": candidates})
        strong = [c for c in candidates if c["home_similarity"] >= .72 and c["away_similarity"] >= .72 and c["same_lagos_date"]]
        category = "A_strong_currently_rejected" if len(strong) == 1 else ("C_ambiguous" if len(strong) > 1 else "B_genuine_absence")
        item = {"forebet_home": forebet.home_team, "forebet_away": forebet.away_team, "draw_probability": forebet.probabilities.draw if forebet.probabilities else None, "prediction_date": str(prediction_date), "current_reason": result.reason, "candidates": candidates}
        report["categories"][category].append(item)
    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
