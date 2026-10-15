"""Application configuration (environment / .env driven)."""
from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]  # .../ForgeAgent


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT_DIR / ".env"), env_prefix="FORGE_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = ROOT_DIR / "data"
    database_url: str | None = None

    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_api_key: str = "sk-local"

    # llama.cpp process management (optional; the app can also attach to an already running server)
    llama_server_path: str = r"C:\AI\llama.cpp\llama-server.exe" if os.name == "nt" else (shutil.which("llama-server") or "")
    models_dir: str = r"C:\AI\Models" if os.name == "nt" else str(Path.home() / "models")
    llama_gpu_layers: int = 99
    llama_extra_args: str = ""

    allowed_origins: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"])
    # When set, the API requires this token (X-Forge-Token header / ?token=). Empty = localhost only, no token.
    api_token: str = ""

    @field_validator("data_dir")
    @classmethod
    def _abs_data_dir(cls, v: Path) -> Path:
        return v if v.is_absolute() else (ROOT_DIR / v).resolve()

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'forge.db').as_posix()}"

    @property
    def logs_dir(self) -> Path:
        p = self.data_dir / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def outputs_dir(self) -> Path:
        p = self.data_dir / "outputs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def screenshots_dir(self) -> Path:
        p = self.data_dir / "screenshots"
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_env() -> EnvSettings:
    return EnvSettings()
