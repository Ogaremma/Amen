"""Acquire Forebet snapshots with installed Chrome and submit a Render dry-run."""
import argparse, asyncio, json, os
from pathlib import Path
from urllib.parse import urlparse
import httpx
from playwright.async_api import async_playwright
from app.services.forebet import parse_forebet_html
from app.services.forebet_dates import future_prediction_dates, future_prediction_urls

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default=os.getenv("AMEN_BACKEND_URL", "https://amen-backend.onrender.com"))
    parser.add_argument("--token", default=os.getenv("FOREBET_INGESTION_TOKEN"), required=False)
    parser.add_argument("--headless", action="store_true", help="Use headless Chrome; headed mode is the proven local path")
    parser.add_argument("--diagnostic-dir", default=None)
    parser.add_argument("--no-submit", action="store_true", help="Acquire and report locally without contacting Render")
    args = parser.parse_args()
    if not args.token and not args.no_submit:
        raise SystemExit("FOREBET_INGESTION_TOKEN or --token is required")
    snapshots = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=args.headless)
        try:
            for prediction_date, url in zip(future_prediction_dates(), future_prediction_urls()):
                context = await browser.new_context(locale="en-US", viewport={"width": 1366, "height": 768})
                page = await context.new_page()
                response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
                html = await page.content()
                markers = [marker for marker in ("captcha", "cloudflare", "access denied", "verify you are human", "just a moment", "attention required") if marker in html.lower()]
                diagnostic = {"url": url, "status": response.status if response else None, "final_url": page.url, "final_hostname": urlparse(page.url).hostname, "title": (await page.title())[:120], "schema_present": await page.locator(".schema").count() > 0, "schema_count": await page.locator(".schema").count(), "challenge_markers": markers, "chrome_channel": "chrome", "headless": args.headless, "fresh_context": True}
                print(json.dumps(diagnostic))
                if args.diagnostic_dir:
                    out = Path(args.diagnostic_dir); out.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(out / f"forebet_{prediction_date.isoformat()}.png"), full_page=False)
                    (out / f"forebet_{prediction_date.isoformat()}.html").write_text(html, encoding="utf-8")
                await page.wait_for_selector(".schema", timeout=60000)
                matches = parse_forebet_html(html, url)
                snapshots.append({"prediction_date": prediction_date.isoformat(), "source_url": url, "matches": [m.model_dump(mode="json") for m in matches]})
                print(json.dumps({"date": prediction_date.isoformat(), "status": response.status if response else None, "matches": len(matches), "draws": sum(m.predicted_result.value == "DRAW" for m in matches)}))
                await page.close()
                await context.close()
        finally:
            await browser.close()
    if args.no_submit:
        return
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{args.backend.rstrip('/')}/api/v1/forebet/acquisition-snapshots", headers={"Authorization": f"Bearer {args.token}"}, json={"snapshots": snapshots, "dry_run": True})
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2, default=str))

if __name__ == "__main__":
    asyncio.run(main())
