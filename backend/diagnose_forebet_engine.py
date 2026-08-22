import asyncio

from app.services.forebet_draw_engine import ForebetDrawEngine
from app.services.forebet_dates import future_prediction_urls


async def main():
    engine = ForebetDrawEngine()

    urls = future_prediction_urls(count=1)

    print("SOURCE URLS:")
    for url in urls:
        print(" ", url)

    print("\nREFRESHING DRAW WINDOW...\n")

    result = await engine.refresh_window(urls)

    print("\n" + "=" * 80)
    print("DRAW WINDOW RESULT")
    print("=" * 80)

    print("ACTIVE COUNT:", result.active_count)

    for day in result.days:
        print("\nDATE:", day.prediction_date)
        print("BOOKING CODE:", day.booking_code)
        print("MATCH COUNT:", len(day.matches))

        for match in day.matches:
            print(
                f"  {match.home_team} vs {match.away_team}"
                f" | event={match.event_id}"
                f" | kickoff={match.kickoff}"
                f" | market={match.market_id}"
                f" | outcome={match.outcome_id}"
                f" | product={match.product_id}"
            )

        if day.diagnostics:
            print("\nDIAGNOSTICS:")
            for diagnostic in day.diagnostics:
                print(" ", diagnostic)


asyncio.run(main())
