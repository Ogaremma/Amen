import asyncio

from app.services.sportybet import get_upcoming_football_events


async def main():
    result = await get_upcoming_football_events()

    print("EVENT COUNT:", len(result.events))

    for event in result.events[:20]:
        print(
            f"EVENT | {event.event_id} | "
            f"{event.home_team} vs {event.away_team} | "
            f"kickoff={event.kickoff} | "
            f"market={event.market_id} | "
            f"draw={event.outcome_draw_id} | "
            f"product={event.product_id}"
        )


asyncio.run(main())
