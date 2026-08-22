import asyncio

from app.services.forebet import (
    fetch_forebet_page,
    parse_forebet_html,
    get_draw_matches,
)
from app.services.forebet_dates import future_prediction_urls


async def main():
    url = future_prediction_urls()[0]

    print("URL:", url)

    html = await fetch_forebet_page(url)

    print("HTML LENGTH:", len(html))
    print("HAS SCHEMA:", ".schema" in html)
    print("RCNT COUNT:", html.count('class="rcnt'))

    matches = parse_forebet_html(html, url)
    draws = get_draw_matches(matches)

    print("PARSED MATCHES:", len(matches))
    print("DRAW MATCHES:", len(draws))

    for match in draws[:10]:
        probability = (
            match.probabilities.draw
            if match.probabilities
            else None
        )

        print(
            f"DRAW | {match.home_team} vs {match.away_team} "
            f"| kickoff={match.kickoff} "
            f"| probability={probability}"
        )


asyncio.run(main())
