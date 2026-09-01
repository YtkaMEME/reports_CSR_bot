from collections.abc import Awaitable, Callable
from typing import Any, Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from src.config import config


class AccessMiddleware(BaseMiddleware):
    """Пропускает только пользователей из allowlist по Telegram username."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = self._get_user(event, data)
        if self._is_allowed(user):
            return await handler(event, data)

        await self._deny(event)
        return None

    def _get_user(self, event: TelegramObject, data: dict[str, Any]) -> Optional[User]:
        if isinstance(event, (Message, CallbackQuery)):
            return event.from_user
        return data.get("event_from_user")

    def _is_allowed(self, user: Optional[User]) -> bool:
        if not user or not user.username:
            return False
        return config.normalize_username(user.username) in config.allowed_usernames

    async def _deny(self, event: TelegramObject) -> None:
        text = "Нет доступа к этому боту."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(text)
