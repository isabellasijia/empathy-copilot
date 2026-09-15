from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    workbook_path: Path = Path(
        os.getenv(
            "EMPATHY_WORKBOOK_PATH",
            str(PROJECT_ROOT / "data" / "official-business-data.xlsx"),
        )
    )
    database_path: Path = Path(
        os.getenv("EMPATHY_DATABASE_PATH", str(PROJECT_ROOT / "runtime" / "empathy.db"))
    )
    uploads_dir: Path = Path(
        os.getenv("EMPATHY_UPLOADS_DIR", str(PROJECT_ROOT / "runtime" / "uploads"))
    )
    frontend_dir: Path = PROJECT_ROOT / "frontend"
    knowledge_dir: Path = PROJECT_ROOT / "data" / "knowledge"
    dashscope_api_key: str | None = os.getenv("DASHSCOPE_API_KEY")
    dashscope_base_url: str = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://ws-cbx8yygvhh809a77.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )
    qwen_text_model: str = os.getenv("QWEN_TEXT_MODEL", "qwen-turbo")
    qwen_omni_model: str = os.getenv(
        "QWEN_OMNI_MODEL",
        os.getenv("QWEN_MODEL", "qwen3-omni-flash-2025-12-01"),
    )
    qwen_timeout_seconds: float = float(os.getenv("QWEN_TIMEOUT_SECONDS", "60"))


settings = Settings()
