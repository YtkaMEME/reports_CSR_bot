from datetime import datetime

from aiogram.types import User

from src.config import config


def log_export_request(user: User, export_name: str) -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    username = f"@{user.username}" if user.username else "-"
    full_name = sanitize_log_value(user.full_name or "-")
    export_name = sanitize_log_value(export_name)

    line = (
        f"{timestamp} | "
        f"user_id={user.id} | "
        f"username={username} | "
        f"full_name={full_name} | "
        f"export={export_name}\n"
    )

    with config.export_requests_log_file.open("a", encoding="utf-8") as log_file:
        log_file.write(line)


def sanitize_log_value(value: str) -> str:
    return " ".join(str(value).split())
