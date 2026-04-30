"""
config/settings.py — Environment-variable driven configuration.

All settings are read from environment variables with the MYPROJECT_ prefix.
Change the prefix to match your project.

Usage:
    settings = Settings.from_env()
    print(settings.llm_model)

Never hardcode secrets. Always read from environment or .env file.
"""

import os
from dataclasses import dataclass


@dataclass
class Settings:
    # LLM
    llm_model: str = "llama3-8b-8192"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096

    # Cache
    cache_dir: str = ".cache"
    cache_ttl_hours: int = 6

    # Agent
    max_iterations: int = 20
    debug: bool = False

    # Data
    data_dir: str = "data"

    @classmethod
    def from_env(cls) -> "Settings":
        """Load settings from environment variables (MYPROJECT_ prefix)."""
        prefix = "MYPROJECT_"

        def env(key: str, default: str = "") -> str:
            return os.environ.get(f"{prefix}{key}", default)

        def env_bool(key: str, default: bool = False) -> bool:
            val = os.environ.get(f"{prefix}{key}", "")
            return val.lower() in ("1", "true", "yes") if val else default

        def env_int(key: str, default: int = 0) -> int:
            val = os.environ.get(f"{prefix}{key}", "")
            try:
                return int(val) if val else default
            except ValueError:
                return default

        def env_float(key: str, default: float = 0.0) -> float:
            val = os.environ.get(f"{prefix}{key}", "")
            try:
                return float(val) if val else default
            except ValueError:
                return default

        return cls(
            llm_model=env("LLM_MODEL", cls.llm_model),
            llm_base_url=env("LLM_BASE_URL", cls.llm_base_url),
            llm_api_key=env("LLM_API_KEY", ""),
            llm_temperature=env_float("LLM_TEMPERATURE", cls.llm_temperature),
            llm_max_tokens=env_int("LLM_MAX_TOKENS", cls.llm_max_tokens),
            cache_dir=env("CACHE_DIR", cls.cache_dir),
            cache_ttl_hours=env_int("CACHE_TTL_HOURS", cls.cache_ttl_hours),
            max_iterations=env_int("MAX_ITERATIONS", cls.max_iterations),
            debug=env_bool("DEBUG", False),
            data_dir=env("DATA_DIR", cls.data_dir),
        )

    def validate(self) -> list[str]:
        """Return list of validation errors (empty = valid)."""
        errors = []
        if not self.llm_api_key:
            errors.append("MYPROJECT_LLM_API_KEY is not set")
        if self.llm_temperature < 0 or self.llm_temperature > 2:
            errors.append(f"LLM_TEMPERATURE must be 0–2, got {self.llm_temperature}")
        return errors
