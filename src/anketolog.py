import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

from src.config import config


BASE_URL = "https://apiv2.anketolog.ru"
SURVEY_LIST_URL = f"{BASE_URL}/survey/manage/list"
SURVEY_REPORT_CREATE_URL = f"{BASE_URL}/survey/report/create"
SURVEY_REPORT_LIST_URL = f"{BASE_URL}/survey/report/list"
SURVEY_FOLDER_LIST_URL = f"{BASE_URL}/survey/folder/list"

REPORT_FORMAT = "excel"
TIMEOUT = 60
DOWNLOAD_TIMEOUT = 120
POLL_INTERVAL = 7
MAX_POLLS = 80
DOWNLOAD_RETRIES = 12
DOWNLOAD_RETRY_DELAY = 5
SURVEY_PAGE_LIMIT = 100
MAX_SURVEY_PAGES = 100


@dataclass(frozen=True)
class DownloadedReport:
    path: Path
    survey_name: str


class AnketologError(Exception):
    """Ошибка при работе с Anketolog."""


def _headers() -> dict[str, str]:
    api_key = os.getenv("ANKETOLOG_TOKEN", "").strip()
    if not api_key:
        raise AnketologError("Не найден ANKETOLOG_TOKEN. Укажите его в .env.")

    return {
        "x-anketolog-apikey": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _proxies() -> Optional[dict[str, str]]:
    if not config.proxy_url:
        return None
    return {
        "http": config.proxy_url,
        "https": config.proxy_url,
    }


def _post_json(url: str, payload: dict[str, Any]) -> Any:
    try:
        response = requests.post(
            url,
            headers=_headers(),
            json=payload,
            proxies=_proxies(),
            timeout=TIMEOUT,
        )
    except requests.RequestException as error:
        raise AnketologError(f"Ошибка запроса к Anketolog: {error}") from error

    if response.status_code >= 400:
        message = _extract_error_message(response)
        if response.status_code == 401:
            raise AnketologError(
                f"Anketolog отклонил API-ключ: {message}. Проверьте ANKETOLOG_TOKEN."
            )
        raise AnketologError(f"Ошибка запроса к Anketolog: HTTP {response.status_code}: {message}")

    try:
        return response.json()
    except ValueError as error:
        raise AnketologError("Anketolog вернул не JSON-ответ.") from error


def _extract_error_message(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:300] or "пустой ответ"

    if isinstance(data, dict):
        message = data.get("message") or data.get("name") or data.get("error")
        if message:
            return str(message)

    return str(data)[:300]


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\\\|?*]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:150] or "anketolog_report"


def normalize_survey_name(name: str) -> str:
    name = (name or "").strip().lower()
    return re.sub(r"\s+", " ", name)


def get_extension(report_format: str) -> str:
    mapping = {
        "excel": "xlsx",
        "csv": "csv",
        "spss": "sav",
        "fpdf": "pdf",
        "fword": "docx",
        "pdf": "pdf",
        "word": "docx",
        "word2": "docx",
        "excelchart": "xlsx",
    }
    return mapping.get(report_format, "bin")


def get_survey_folders() -> Any:
    return _post_json(SURVEY_FOLDER_LIST_URL, {})


def flatten_folder_names(folder_tree: Any) -> list[str]:
    names: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            name = node.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(folder_tree)
    return names


def get_survey_list_page(offset: int = 0, folder_name: Optional[str] = None) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"limit": SURVEY_PAGE_LIMIT, "offset": offset}
    if folder_name:
        payload["folder"] = folder_name

    data = _post_json(SURVEY_LIST_URL, payload)
    if not isinstance(data, list):
        raise AnketologError(f"Ожидался список анкет, но пришло: {type(data).__name__}.")
    return [survey for survey in data if isinstance(survey, dict)]


def get_survey_list(folder_path: Optional[str] = None) -> list[dict[str, Any]]:
    surveys_by_id: dict[Any, dict[str, Any]] = {}
    folder_name = get_folder_name(folder_path) if folder_path else None

    for page_index in range(MAX_SURVEY_PAGES):
        page = get_survey_list_page(
            offset=page_index * SURVEY_PAGE_LIMIT,
            folder_name=folder_name,
        )
        previous_count = len(surveys_by_id)

        for survey in page:
            survey_id = survey.get("id")
            if survey_id is not None:
                surveys_by_id[survey_id] = survey

        if len(page) < SURVEY_PAGE_LIMIT or len(surveys_by_id) == previous_count:
            break

    return list(surveys_by_id.values())


def get_folder_name(folder_path: str) -> str:
    return folder_path.rstrip("/").split("/")[-1].strip()


def get_survey_folder_path(survey: dict[str, Any]) -> str:
    folder = survey.get("folder")
    if isinstance(folder, dict):
        folder_path = folder.get("path")
        if isinstance(folder_path, str):
            return folder_path
    return ""


def is_expected_folder(survey: dict[str, Any], folder_path: Optional[str]) -> bool:
    if not folder_path:
        return True
    return normalize_survey_name(get_survey_folder_path(survey)) == normalize_survey_name(folder_path)


def find_survey_by_name(
    surveys: list[dict[str, Any]],
    survey_name: str,
    folder_path: Optional[str] = None,
) -> dict[str, Any]:
    exact_matches: list[dict[str, Any]] = []
    partial_matches: list[dict[str, Any]] = []
    normalized_search = normalize_survey_name(survey_name)

    for survey in surveys:
        if not is_expected_folder(survey, folder_path):
            continue

        current_name = (survey.get("settings") or {}).get("name")
        if not current_name:
            continue

        normalized_current = normalize_survey_name(current_name)
        if normalized_current == normalized_search:
            exact_matches.append(survey)
        elif normalized_search in normalized_current:
            partial_matches.append(survey)

    if len(exact_matches) == 1:
        return exact_matches[0]

    if len(exact_matches) > 1:
        raise AnketologError("Найдено несколько анкет с точным совпадением: " + _format_matches(exact_matches))

    if len(partial_matches) == 1:
        return partial_matches[0]

    if len(partial_matches) > 1:
        raise AnketologError("Найдено несколько анкет с частичным совпадением: " + _format_matches(partial_matches))

    folder_message = f' в папке "{folder_path}"' if folder_path else ""
    raise AnketologError(f'Анкета с названием "{survey_name}"{folder_message} не найдена.')


def _format_matches(surveys: list[dict[str, Any]]) -> str:
    return ", ".join(
        f'{survey.get("id")}:{(survey.get("settings") or {}).get("name")}'
        for survey in surveys[:10]
    )


def get_folder_hint() -> str:
    try:
        folder_names = flatten_folder_names(get_survey_folders())
    except AnketologError:
        return ""

    if not folder_names:
        return ""

    return f" Доступные папки: {', '.join(folder_names[:20])}."


def locate_survey_by_name(survey_name: str, folder_path: Optional[str] = None) -> dict[str, Any]:
    surveys = get_survey_list(folder_path=folder_path)
    if not surveys:
        raise AnketologError("Не удалось получить список анкет из Anketolog.")

    try:
        return find_survey_by_name(surveys, survey_name, folder_path=folder_path)
    except AnketologError as error:
        message = str(error)
        if "не найдена" in message:
            raise AnketologError(message + get_folder_hint()) from error
        raise


def create_report(survey_id: Any) -> dict[str, Any]:
    data = _post_json(
        SURVEY_REPORT_CREATE_URL,
        {"survey_id": survey_id, "format": REPORT_FORMAT},
    )
    if not isinstance(data, dict):
        raise AnketologError("Не удалось создать отчет: неверный формат ответа.")
    return data


def get_report_list(survey_id: Any) -> list[dict[str, Any]]:
    data = _post_json(SURVEY_REPORT_LIST_URL, {"survey_id": survey_id})
    if not isinstance(data, list):
        raise AnketologError("Не удалось получить список отчетов.")
    return [report for report in data if isinstance(report, dict)]


def find_report_by_id(report_list: list[dict[str, Any]], report_id: Any) -> Optional[dict[str, Any]]:
    for report in report_list:
        if report.get("id") == report_id:
            return report
    return None


def wait_until_report_ready(survey_id: Any, report_id: Any) -> dict[str, Any]:
    if report_id is None:
        raise AnketologError("Anketolog не вернул id созданного отчета.")

    for _ in range(MAX_POLLS):
        report = find_report_by_id(get_report_list(survey_id), report_id)
        if not report:
            time.sleep(POLL_INTERVAL)
            continue

        status = report.get("status")
        url = report.get("url")

        if status == "complete" and url:
            return report

        if status == "fail":
            raise AnketologError("Формирование отчета завершилось с ошибкой.")

        time.sleep(POLL_INTERVAL)

    raise AnketologError("Отчет не стал готовым за отведенное время.")


def build_report_filename(survey_name: str, survey_id: Any, extension: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{sanitize_filename(survey_name)}_{survey_id}_{timestamp}.{extension}"
    return config.download_dir / filename


def remove_file_safely(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def download_file(url: str, filename: Path) -> Path:
    browser_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "ru,en;q=0.9",
        "Referer": "https://anketolog.ru/",
        "Connection": "keep-alive",
    }

    filename.parent.mkdir(parents=True, exist_ok=True)
    last_error: Optional[Exception] = None

    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            with requests.Session() as session:
                with session.get(
                    url,
                    headers=browser_headers,
                    proxies=_proxies(),
                    stream=True,
                    allow_redirects=True,
                    timeout=DOWNLOAD_TIMEOUT,
                ) as response:
                    response.raise_for_status()
                    with filename.open("wb") as file_obj:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                file_obj.write(chunk)
            return filename
        except requests.RequestException as error:
            last_error = error
            remove_file_safely(filename)
            if attempt == DOWNLOAD_RETRIES:
                break
            time.sleep(DOWNLOAD_RETRY_DELAY)
        except OSError as error:
            remove_file_safely(filename)
            raise AnketologError(f"Не удалось сохранить отчет: {error}") from error

    raise AnketologError(f"Не удалось скачать отчет по ссылке: {last_error}")


def download_report_by_survey_name(survey_name: str, folder_path: Optional[str] = None) -> DownloadedReport:
    survey = locate_survey_by_name(survey_name, folder_path=folder_path)
    survey_id = survey.get("id")
    survey_real_name = (survey.get("settings") or {}).get("name") or survey_name

    if survey_id is None:
        raise AnketologError(f'У анкеты "{survey_real_name}" отсутствует id.')

    report = create_report(survey_id)
    report_status = report.get("status")
    report_url = report.get("url")

    if report_status == "complete" and report_url:
        ready_report = report
    else:
        ready_report = wait_until_report_ready(survey_id, report.get("id"))

    ready_url = ready_report.get("url")
    if not ready_url:
        raise AnketologError("Отчет помечен как готовый, но URL отсутствует.")

    extension = get_extension(ready_report.get("format", REPORT_FORMAT))
    filename = build_report_filename(survey_real_name, survey_id, extension)
    path = download_file(ready_url, filename)

    return DownloadedReport(path=path, survey_name=survey_real_name)
