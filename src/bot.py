import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.anketolog import AnketologError, download_report_by_survey_name
from src.export_log import log_export_request


logger = logging.getLogger(__name__)
router = Router()

LECTURE_CALLBACK = "export:lecture"
MASTERY_CALLBACK = "export:mastery"
DONE_MESSAGE_TTL_SECONDS = 3


@dataclass(frozen=True)
class SurveyConfig:
    name: str
    folder_path: str


LECTURE_FOLDER = "Мои анкеты/Академия Меганом 2026/КСР/Лекции для студентов (вузы)"
MASTERY_FOLDER = "Мои анкеты/Академия Меганом 2026/КСР/Культура мастерства"

PROGRAM_SURVEYS = {
    LECTURE_CALLBACK: (
        SurveyConfig(
            name="Культурно-просветительская лекция для студентов  2026 год",
            folder_path=LECTURE_FOLDER,
        ),
    ),
    MASTERY_CALLBACK: (
        SurveyConfig(
            name="Культура мастерства до программы 2026",
            folder_path=MASTERY_FOLDER,
        ),
        SurveyConfig(
            name="Культура мастерства итоговый 2026",
            folder_path=MASTERY_FOLDER,
        ),
    ),
}

PROGRAM_TITLES = {
    LECTURE_CALLBACK: "Культурно-просветительская лекция для студентов 2026 год",
    MASTERY_CALLBACK: "Культура мастерства 2026",
}


def build_program_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Культурно-просветительская лекция для студентов 2026 год",
                    callback_data=LECTURE_CALLBACK,
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Культура мастерства 2026",
                    callback_data=MASTERY_CALLBACK,
                ),
            ],
        ]
    )


@router.message()
async def show_export_keyboard(message: Message) -> None:
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


@router.callback_query(F.data.in_([LECTURE_CALLBACK, MASTERY_CALLBACK]))
async def create_exports(callback_query: CallbackQuery) -> None:
    await callback_query.answer()

    callback_data = callback_query.data or ""
    program_title = PROGRAM_TITLES[callback_data]
    survey_names = PROGRAM_SURVEYS[callback_data]
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
