import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.anketolog import AnketologError, download_report_by_survey_name
from src.config import config
from src.export_log import log_export_request


logger = logging.getLogger(__name__)
router = Router()

LECTURE_CALLBACK = "export:lecture"
FOK_CALLBACK = "export:fok"
MASTERY_CALLBACK = "export:mastery"
DONE_MESSAGE_TTL_SECONDS = 3


@dataclass(frozen=True)
class SurveyConfig:
    name: str
    folder_path: str


PROGRAM_SURVEYS = {
    LECTURE_CALLBACK: tuple(
        SurveyConfig(name=name, folder_path=config.lecture_survey_folder)
        for name in config.lecture_surveys
    ),
    FOK_CALLBACK: tuple(
        SurveyConfig(name=name, folder_path=config.fok_survey_folder)
        for name in config.fok_surveys
    ),
    MASTERY_CALLBACK: tuple(
        SurveyConfig(name=name, folder_path=config.mastery_survey_folder)
        for name in config.mastery_surveys
    ),
}

PROGRAM_TITLES = {
    LECTURE_CALLBACK: "Культурно-просветительская лекция для студентов 2026 год",
    FOK_CALLBACK: "ФОК КСР Встреча мэтра в сфере культуры с молодыми участниками форума",
    MASTERY_CALLBACK: "Культура мастерства 2026",
}


def build_program_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for callback_data, title in PROGRAM_TITLES.items():
        if PROGRAM_SURVEYS[callback_data]:
            buttons.append(
                [InlineKeyboardButton(text=title, callback_data=callback_data)]
            )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message()
async def show_export_keyboard(message: Message) -> None:
    if not any(PROGRAM_SURVEYS.values()):
        await message.answer("Сейчас нет доступных выгрузок.")
        return

    await message.answer(
        "Выберите программу для формирования выгрузки:",
        reply_markup=build_program_keyboard(),
    )


def delete_local_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logger.exception("Could not delete export file: %s", path)


def cleanup_local_files(paths: list[Path]) -> None:
    for path in paths:
        delete_local_file(path)


async def delete_message_safely(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        logger.debug("Could not delete bot message", exc_info=True)


async def show_done_and_delete(status_message: Message) -> None:
    try:
        await status_message.edit_text("Готово. Выгрузка отправлена.")
    except Exception:
        logger.debug("Could not edit status message", exc_info=True)

    await asyncio.sleep(DONE_MESSAGE_TTL_SECONDS)
    await delete_message_safely(status_message)


@router.callback_query(F.data.in_([LECTURE_CALLBACK, FOK_CALLBACK, MASTERY_CALLBACK]))
async def create_exports(callback_query: CallbackQuery) -> None:
    callback_data = callback_query.data or ""
    program_title = PROGRAM_TITLES[callback_data]
    survey_names = PROGRAM_SURVEYS[callback_data]
    if not survey_names:
        await callback_query.answer("Эта выгрузка отключена.", show_alert=True)
        return

    await callback_query.answer()
    chat_id = callback_query.message.chat.id

    status_message = await callback_query.message.answer(
        f"Формирую выгрузку: {program_title}"
    )
    downloaded_paths: list[Path] = []

    try:
        for index, survey_config in enumerate(survey_names, start=1):
            await status_message.edit_text(
                f"Создаю отчет в Anketolog ({index}/{len(survey_names)}):\n{survey_config.name}"
            )

            report = await asyncio.to_thread(
                download_report_by_survey_name,
                survey_config.name,
                survey_config.folder_path,
            )
            downloaded_paths.append(report.path)

            try:
                log_export_request(callback_query.from_user, report.survey_name)
            except OSError:
                logger.exception("Could not write export request log")

            await callback_query.bot.send_document(
                chat_id=chat_id,
                document=FSInputFile(report.path),
                caption=f"Выгрузка: {report.survey_name}",
            )
            delete_local_file(report.path)

        await show_done_and_delete(status_message)
    except AnketologError as error:
        logger.exception("Anketolog export failed")
        await status_message.edit_text(f"Не удалось сформировать выгрузку: {error}")
    except OSError as error:
        logger.exception("Export file handling failed")
        await status_message.edit_text(f"Не удалось отправить файл выгрузки: {error}")
    except TelegramAPIError as error:
        logger.exception("Telegram export sending failed")
        await status_message.edit_text(f"Не удалось отправить выгрузку в Telegram: {error}")
    finally:
        cleanup_local_files(downloaded_paths)
