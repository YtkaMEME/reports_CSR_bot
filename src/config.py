import json
import os
import importlib.util
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Ошибка конфигурации бота."""


class Config:
    """Конфигурация приложения и загрузка переменных из .env."""

    def __init__(self) -> None:
        self.project_root = Path(__file__).resolve().parents[1]
        self._load_env_file(self.project_root / ".env")

        self.bot_token = os.getenv("BOT_TOKEN", "").strip()
        self.proxy_url = self._load_proxy_url()
        self.download_dir = self.project_root / "downloads"
        self.logs_dir = self.project_root / "logs"
        self.export_requests_log_file = self.logs_dir / "export_requests.log"
        self.allowed_usernames_file = self.project_root / "config" / "allowed_usernames.json"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.allowed_usernames = self._load_allowed_usernames()

    def _load_env_file(self, env_path: Path) -> None:
        if not env_path.exists():
            return

        try:
            with env_path.open("r", encoding="utf-8") as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue

                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError as error:
            raise ConfigError(f"Не удалось прочитать .env: {error}") from error

    def _load_allowed_usernames(self) -> set[str]:
        usernames = []
        usernames.extend(self._load_allowed_usernames_from_json())
        usernames.extend(os.getenv("ALLOWED_USERNAMES", "").split(","))
        return {
            normalized
            for username in usernames
            if (normalized := self.normalize_username(username))
        }

    def _load_allowed_usernames_from_json(self) -> list[str]:
        if not self.allowed_usernames_file.exists():
            self.allowed_usernames_file.parent.mkdir(parents=True, exist_ok=True)
            with self.allowed_usernames_file.open("w", encoding="utf-8") as json_file:
                json.dump([], json_file, ensure_ascii=False, indent=2)
            return []

        try:
            with self.allowed_usernames_file.open("r", encoding="utf-8") as json_file:
                data: Any = json.load(json_file)
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(f"Не удалось прочитать список пользователей: {error}") from error

        if isinstance(data, list):
            return [str(username) for username in data]

        if isinstance(data, dict) and isinstance(data.get("usernames"), list):
            return [str(username) for username in data["usernames"]]

        raise ConfigError(
            "allowed_usernames.json должен быть списком username или объектом с ключом usernames."
        )

    @staticmethod
    def normalize_username(username: object) -> str:
        return str(username).strip().lstrip("@").lower()

    def _load_proxy_url(self) -> str:
        env_proxy = os.getenv("PROXY", "").strip()
        if env_proxy:
            return env_proxy

        proxy_file = self.project_root / "PROXY.py"
        if not proxy_file.exists():
            return ""

        spec = importlib.util.spec_from_file_location("bot_ksr_proxy", proxy_file)
        if spec is None or spec.loader is None:
            raise ConfigError("Не удалось загрузить PROXY.py.")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return str(getattr(module, "PROXY", "")).strip()


config = Config()
