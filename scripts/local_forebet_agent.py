"""Acquire Forebet snapshots with the shared browser fallback and submit trusted raw HTML."""
import argparse, asyncio, json, os
from pathlib import Path
import httpx
from app.services.forebet import _fetch_forebet_page_browser_sync, parse_forebet_html
from app.services.forebet_dates import future_prediction_dates, future_prediction_urls

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default=os.getenv("AMEN_BACKEND_URL"), required=False)
    parser.add_argument("--token", default=os.getenv("FOREBET_INGESTION_TOKEN"), required=False)
    parser.add_argument("--headless", action="store_true", help="Use headless Chrome; headed mode is the proven local path")
    parser.add_argument("--diagnostic-dir", default=None)
    parser.add_argument("--no-submit", action="store_true", help="Acquire and report locally without contacting Render")
    args = parser.parse_args()
    if not args.token and not args.no_submit:
        raise SystemExit("FOREBET_INGESTION_TOKEN or --token is required")
    if not args.backend and not args.no_submit:
        raise SystemExit("AMEN_BACKEND_URL or --backend is required")
    snapshots = []
    for prediction_date, url in zip(future_prediction_dates(), future_prediction_urls()):
        html = _fetch_forebet_page_browser_sync(url)
        matches = parse_forebet_html(html, url)
        snapshots.append({"prediction_date": prediction_date.isoformat(), "source_url": url, "raw_html": html, "matches": [m.model_dump(mode="json") for m in matches]})
        print(json.dumps({"date": prediction_date.isoformat(), "matches": len(matches), "draws": sum(m.predicted_result.value == "DRAW" for m in matches)}))
    if args.no_submit:
        return
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{args.backend.rstrip('/')}/api/v1/forebet/acquisition-snapshots", headers={"Authorization": f"Bearer {args.token}"}, json={"snapshots": snapshots, "dry_run": True})
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2, default=str))

if __name__ == "__main__":
    asyncio.run(main())
