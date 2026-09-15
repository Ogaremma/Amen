from __future__ import annotations

import argparse
import secrets

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field

from app.config.settings import get_settings
from app.prediction_daily_runner import execute as execute_daily_prediction_runner


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class PredictionDailyRunRequest(BaseModel):
    chat_id: int | str | None = None
    page_size: int | None = Field(default=None, ge=1, le=100)
    max_pages: int | None = Field(default=None, ge=1)


def _verify_admin_token(authorization: str | None) -> None:
    expected = get_settings().prediction_daily_token
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Admin authentication required")


@router.post("/prediction-daily/run", include_in_schema=False)
async def run_prediction_daily(
    request: PredictionDailyRunRequest,
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict:
    _verify_admin_token(authorization)
    try:
        summary, exit_code = await execute_daily_prediction_runner(
            argparse.Namespace(
                chat_id=request.chat_id,
                page_size=request.page_size,
                max_pages=request.max_pages,
            )
        )
    except SystemExit as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    response.status_code = 200 if exit_code == 0 else 502
    return {"exit_code": exit_code, **summary}
