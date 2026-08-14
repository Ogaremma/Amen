from fastapi import APIRouter, Depends

from app.schemas.history import HistoryItem
from app.services.history_store import history_store
from app.services.telegram_auth import TelegramUser
from app.services.telegram_identity import verified_telegram_user

router = APIRouter(prefix="/api/v1/history", tags=["history"])


@router.get("", response_model=list[HistoryItem])
async def get_history(user: TelegramUser = Depends(verified_telegram_user)):
    return history_store.list(user.telegram_user_id)
