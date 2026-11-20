"""Runtime configuration loaded strictly from environment variables / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    firebase_service_account_path: str | None
    firebase_service_account_b64: str | None
    gemini_api_key: str | None
    gemini_model: str
    smtp_host: str
    smtp_port: int
    smtp_user: str | None
    smtp_app_password: str | None
    report_recipient_email: str | None
    store_name: str
    timezone: str
    output_dir: Path

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "Settings":
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        return cls(
            firebase_service_account_path=_opt("FIREBASE_SERVICE_ACCOUNT_PATH"),
            firebase_service_account_b64=_opt("FIREBASE_SERVICE_ACCOUNT_B64"),
            gemini_api_key=_opt("GEMINI_API_KEY"),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
            smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com").strip() or "smtp.gmail.com",
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=_opt("SMTP_USER"),
            smtp_app_password=_opt("SMTP_APP_PASSWORD"),
            report_recipient_email=_opt("REPORT_RECIPIENT_EMAIL"),
            store_name=os.getenv("STORE_NAME", "Ely's Fresh Fruits & Veggies (ely.pos)").strip(),
            timezone=os.getenv("TIMEZONE", "Asia/Manila").strip() or "Asia/Manila",
            output_dir=Path(os.getenv("OUTPUT_DIR", "output").strip() or "output"),
        )


def _opt(key: str) -> str | None:
    value = os.getenv(key)
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None
