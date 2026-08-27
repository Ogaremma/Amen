from __future__ import annotations

import re
import asyncio
import logging
import time
import threading
from datetime import date, datetime
from typing import Iterable
from urllib.parse import urljoin, urlparse

import httpx
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
from bs4 import BeautifulSoup

from app.config.settings import get_settings
from app.schemas.forebet import (
    ForebetMatch,
    ForebetPredictionResult,
    ForebetProbability,
)

logger = logging.getLogger("amen.forebet_acquisition")


class ForebetAcquisitionError(RuntimeError):
    """Raised when Forebet returns a response that cannot be parsed safely."""


class ForebetAccessDeniedError(ForebetAcquisitionError):
    """Raised when Forebet explicitly rejects the request."""


class ForebetBrowserChallengeError(ForebetAcquisitionError):
    """Raised when browser acquisition reaches an access challenge."""


_browser_semaphore = asyncio.Semaphore(1)
_clearance_lock = threading.Lock()
_clearance_cookies: list[dict] = []


def _active_clearance_cookies() -> list[dict]:
    now = time.time()
    with _clearance_lock:
        return [dict(cookie) for cookie in _clearance_cookies if not cookie.get("expires") or cookie["expires"] > now]


def _remember_clearance_cookies(cookies: list[dict]) -> None:
    retained = [cookie for cookie in cookies if cookie.get("name") in {"cf_clearance", "__cf_bm"}]
    if retained:
        with _clearance_lock:
            _clearance_cookies[:] = retained


def _redirect_count(request) -> int:
    count = 0
    current = request.redirected_from
    while current is not None:
        count += 1
        current = current.redirected_from
    return count


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


def _parse_forebet_kickoff(time_node) -> date | datetime | None:
    """Parse Forebet's startDate, including the visible local kickoff time.

    Forebet snapshots commonly put only YYYY-MM-DD in the schema datetime
    attribute and put the actual HH:MM in ``.date_bah``.
    """
    if time_node is None:
        return None
    visible = _text(time_node.select_one(".date_bah"))
    if visible:
        for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(visible, fmt)
                return parsed if "%H" in fmt else parsed.date()
            except ValueError:
                continue
    return _parse_kickoff(time_node.get("datetime"))


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
            kickoff=_parse_forebet_kickoff(kickoff_node),
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


def rank_draw_matches(matches: Iterable[ForebetMatch], limit: int | None = None) -> list[ForebetMatch]:
    """Rank explicit DRAW predictions by Forebet draw probability.

    Matches without a published draw probability sort after scored matches and
    retain stable source order. The limit is deliberately explicit/configured.
    """
    draws = get_draw_matches(matches)
    ranked = sorted(enumerate(draws), key=lambda item: (item[1].probabilities.draw if item[1].probabilities and item[1].probabilities.draw is not None else float("-inf"), -item[0]), reverse=True)
    return [match for _, match in ranked] if limit is None else [match for _, match in ranked[:limit]]


def _forebet_headers(url: str, user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": urljoin(url, "/en/"),
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }


def _validate_forebet_html(html: str, content_type: str | None) -> None:
    if content_type and "html" not in content_type.lower():
        raise ForebetAcquisitionError(f"Forebet returned unsupported content type: {content_type}")
    lowered = html.lower()
    if not html.strip() or "<html" not in lowered:
        raise ForebetAcquisitionError("Forebet returned an empty or malformed HTML response")


def _validate_browser_forebet_html(html: str, final_url: str, expected_host: str) -> None:
    from urllib.parse import urlparse
    if urlparse(final_url).hostname != expected_host:
        raise ForebetAcquisitionError("Forebet browser navigation left the configured domain")
    lowered = html.lower()
    challenge_markers = ("captcha", "verify you are human", "access denied", "cloudflare ray id", "attention required", "just a moment")
    if any(marker in lowered for marker in challenge_markers):
        raise ForebetBrowserChallengeError("Forebet presented an access-denied or CAPTCHA challenge")
    _validate_forebet_html(html, "text/html")
    if "forebet" not in lowered:
        raise ForebetAcquisitionError("Browser response was not recognizable Forebet HTML")
    # A challenge/interstitial can contain the Forebet brand while still
    # lacking the fixture table consumed by the pure parser. Treat it as an
    # access challenge rather than reporting a misleading acquisition success.
    if not re.search(r'class=["\'][^"\']*schema[^"\']*["\']', html, re.IGNORECASE):
        raise ForebetBrowserChallengeError("Forebet browser response contained no fixture table")


def _validate_and_log_browser_page(
    html: str,
    final_url: str,
    expected_host: str,
    *,
    title: str,
    elapsed_ms: int,
    schema_count: int,
    fixture_rows: int,
) -> None:
    final_host = urlparse(final_url).hostname
    validation_rule = "valid"
    challenge_indicator = None
    lowered = html.lower()
    if final_host != expected_host:
        validation_rule = "wrong_hostname"
    else:
        challenge_indicator = next(
            (marker for marker in ("captcha", "verify you are human", "access denied", "cloudflare ray id", "attention required", "just a moment") if marker in lowered),
            None,
        )
        if challenge_indicator:
            validation_rule = "challenge_indicator"
        elif not html.strip() or "<html" not in lowered:
            validation_rule = "empty_or_malformed"
        elif "forebet" not in lowered:
            validation_rule = "missing_forebet_text"
        elif not re.search(r'class=["\'][^"\']*schema[^"\']*["\']', html, re.IGNORECASE):
            validation_rule = "missing_schema_class"

    logger.warning(
        "browser_validation outcome=%s rule=%s challenge_indicator=%r title=%r "
        "final_url=%s final_host=%s elapsed_ms=%s schema_count=%s fixture_rows=%s",
        "success" if validation_rule == "valid" else "rejected",
        validation_rule,
        challenge_indicator,
        title,
        final_url,
        final_host,
        elapsed_ms,
        schema_count,
        fixture_rows,
    )
    _validate_browser_forebet_html(html, final_url, expected_host)


def _wait_for_browser_content(page, timeout_ms: int) -> None:
    """Wait for an actual Forebet fixture row, not merely an interstitial body."""
    page.wait_for_selector(".schema > .rcnt", timeout=min(timeout_ms, 20000))


def _close_browser_resources(context, browser) -> None:
    try:
        if context is not None:
            context.close()
    finally:
        browser.close()


def _fetch_forebet_page_browser_sync(url: str) -> str:
    """Acquire one Forebet page using synchronous Playwright.

    Playwright's async transport cannot spawn its Node driver correctly under
    the Windows Proactor event loop used by this development environment.
    Running synchronous Playwright in a worker thread avoids that limitation
    while keeping the public acquisition API fully async.
    """
    from urllib.parse import urlparse

    settings = get_settings()
    expected_host = urlparse(settings.forebet_base_url).hostname

    if urlparse(url).hostname != expected_host:
        raise ForebetAcquisitionError(
            "Browser fallback URL must belong to the configured Forebet domain"
        )

    timeout_ms = int(settings.forebet_browser_timeout * 1000)

    logger.info(
        "browser_fallback_attempted host=%s transport=sync_thread",
        expected_host,
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        context = None
        try:
            context = browser.new_context(
                user_agent=settings.forebet_user_agent,
                locale="en-US",
            )
            prior_cookies = _active_clearance_cookies()
            if prior_cookies:
                context.add_cookies(prior_cookies)

            page = context.new_page()

            started = time.monotonic()

            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )

            try:
                _wait_for_browser_content(page, timeout_ms)
            except PlaywrightTimeoutError:
                pass

            html = page.content()
            title = page.title().strip()[:120]

            has_schema = page.locator(".schema").count() > 0
            fixture_rows = page.locator(".schema > .rcnt").count()

            content_type = (
                response.headers.get("content-type")
                if response
                else None
            )

            final_host = urlparse(page.url).hostname

            redirect_count = (
                _redirect_count(response.request)
                if response
                else 0
            )

            _validate_and_log_browser_page(
                html,
                page.url,
                expected_host or "",
                title=title,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                schema_count=int(has_schema),
                fixture_rows=fixture_rows,
            )
            _remember_clearance_cookies(context.cookies())

            logger.info(
                "browser_fallback_success host=%s transport=sync_thread",
                expected_host,
            )

            return html

        finally:
            _close_browser_resources(context, browser)


async def fetch_forebet_page_browser(url: str) -> str:
    """Async wrapper around the Windows-safe synchronous Playwright fallback."""

    async with _browser_semaphore:
        try:
            return await asyncio.to_thread(
                _fetch_forebet_page_browser_sync,
                url,
            )

        except PlaywrightTimeoutError as exc:
            logger.warning(
                "browser_fallback_failure reason=timeout host=%s",
                urlparse(url).hostname,
            )
            raise ForebetAcquisitionError(
                "Forebet browser fallback timed out"
            ) from exc

        except ForebetAcquisitionError:
            logger.warning(
                "browser_fallback_failure reason=validation host=%s",
                urlparse(url).hostname,
            )
            raise

        except Exception as exc:
            logger.warning(
                "browser_fallback_failure reason=%s host=%s",
                type(exc).__name__,
                urlparse(url).hostname,
            )
            raise ForebetAcquisitionError(
                f"Forebet browser fallback failed: {type(exc).__name__}"
            ) from exc


async def fetch_forebet_page(url: str, *, client: httpx.AsyncClient | None = None) -> str:
    settings = get_settings()
    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(settings.forebet_timeout),
        headers=_forebet_headers(url, settings.forebet_user_agent),
        follow_redirects=True,
    )
    try:
        for attempt in range(settings.forebet_retries + 1):
            try:
                for cookie in _active_clearance_cookies():
                    kwargs = {"path": cookie.get("path", "/")}
                    if cookie.get("domain"):
                        kwargs["domain"] = cookie["domain"]
                    http_client.cookies.set(cookie["name"], cookie["value"], **kwargs)
                response = await http_client.get(url)
                if response.status_code == 403:
                    logger.warning("http_403 host=%s", response.request.url.host)
                    if not settings.forebet_browser_fallback_enabled:
                        raise ForebetAccessDeniedError("Forebet rejected the request with HTTP 403")
                    try:
                        return await fetch_forebet_page_browser(url)
                    except ForebetAcquisitionError as exc:
                        raise ForebetAccessDeniedError(f"Forebet HTTP 403 and browser fallback failed: {exc}") from exc
                response.raise_for_status()
                _validate_forebet_html(response.text, response.headers.get("content-type"))
                logger.info("http_success host=%s", response.request.url.host)
                return response.text
            except ForebetAccessDeniedError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                if attempt >= settings.forebet_retries:
                    raise
                await asyncio.sleep(settings.forebet_retry_backoff * (2 ** attempt))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {429, 500, 502, 503, 504} or attempt >= settings.forebet_retries:
                    raise
                await asyncio.sleep(settings.forebet_retry_backoff * (2 ** attempt))
        raise ForebetAcquisitionError("Forebet acquisition exhausted without a response")
    finally:
        if owns_client:
            await http_client.aclose()
