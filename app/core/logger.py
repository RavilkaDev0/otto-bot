import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
LOGS_DIR = BASE_DIR / "logs"

MAIN_LOG_FILE = LOGS_DIR / "otto_bot.log"
EXTERNAL_API_LOG_FILE = LOGS_DIR / "external_api.log"

LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5


def format_log_payload(payload: object) -> str:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )

    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    except TypeError:
        return str(payload)


def create_file_handler(
    filename: Path,
    formatter: logging.Formatter,
) -> logging.Handler:
    try:
        handler = RotatingFileHandler(
            filename=filename,
            maxBytes=MAX_LOG_SIZE,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setLevel(LOG_LEVEL)
        handler.setFormatter(formatter)
        return handler
    except (PermissionError, OSError):
        return logging.NullHandler()


def create_console_handler(formatter: logging.Formatter) -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(LOG_LEVEL)
    handler.setFormatter(formatter)

    return handler


def setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt=LOG_FORMAT,
        datefmt=DATE_FORMAT,
    )

    # Вывод логов в терминал
    console_handler = create_console_handler(formatter)

    # Основной файл logs/otto_bot.log
    main_file_handler = create_file_handler(
        MAIN_LOG_FILE,
        formatter,
    )

    # Отдельный файл logs/external_api.log
    external_api_handler = create_file_handler(
        EXTERNAL_API_LOG_FILE,
        formatter,
    )

    # Главный логгер приложения
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(LOG_LEVEL)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(main_file_handler)

    # Отдельный логгер external_api
    external_api_logger = logging.getLogger("external_api")
    external_api_logger.handlers.clear()
    external_api_logger.setLevel(LOG_LEVEL)
    external_api_logger.addHandler(create_console_handler(formatter))
    external_api_logger.addHandler(external_api_handler)
    external_api_logger.propagate = False

    # Убираем лишний шум библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


setup_logging()
