import asyncio

from app.services.forebet import (
    fetch_forebet_page,
    parse_forebet_html,
    get_draw_matches,
)
from app.services.forebet_dates import future_prediction_urls
from app.services.sportybet import get_upcoming_football_events
from app.services.fixture_matching import match_forebet_fixtures


async def main():
    url = future_prediction_urls()[0]

    print("FOREBET URL:", url)

    # 1. Get Forebet
    html = await fetch_forebet_page(url)
    forebet_matches = parse_forebet_html(html, url)
    draws = get_draw_matches(forebet_matches)

    print("FOREBET MATCHES:", len(forebet_matches))
    print("FOREBET DRAWS:", len(draws))

    # 2. Get SportyBet
    sportybet = await get_upcoming_football_events()

    print("SPORTYBET EVENTS:", len(sportybet.events))

    # 3. Match
    results = match_forebet_fixtures(draws, sportybet.events)

    print("\nMATCH RESULTS")
    print("=" * 100)

    for result in results:
        fb = result.forebet_match
        sb = result.sportybet_event

        print(
            f"\n{fb.home_team} vs {fb.away_team}"
            f"\n  STATUS: {result.status.value}"
            f"\n  REASON: {result.reason}"
        )

        if sb:
            print(
                f"  SPORTYBET: {sb.home_team} vs {sb.away_team}"
                f"\n  EVENT ID: {sb.event_id}"
                f"\n  KICKOFF: {sb.kickoff}"
                f"\n  MARKET: {sb.market_id}"
                f"\n  DRAW: {sb.outcome_draw_id}"
                f"\n  PRODUCT: {sb.product_id}"
            )

    matched = [
        r for r in results
        if r.sportybet_event is not None
    ]

    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print("Forebet draws:", len(draws))
    print("Results:", len(results))
    print("Matched:", len(matched))
    print("Unmatched:", len(results) - len(matched))


asyncio.run(main())
