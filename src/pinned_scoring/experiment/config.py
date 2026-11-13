"""Model configuration: gitignored results/config.json, overridden by environment variables."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = ROOT / "results" / "config.json"
DEFAULT_ENDPOINT = "https://model.example.invalid/"
DEFAULT_API_VERSION = "2024-10-21"


@dataclass(frozen=True)
class ExperimentConfig:
    endpoint: str = DEFAULT_ENDPOINT
    api_version: str = DEFAULT_API_VERSION
    strong: str = "gpt-4.1"
    small: str = "gpt-4.1-mini"
    temperature: float = 0.0
    max_retries: int = 4

    @property
    def default_models(self) -> tuple:
        return (self.strong, self.small)


def load_config(path=DEFAULT_CONFIG_PATH, env=None) -> ExperimentConfig:
    env = os.environ if env is None else env
    data = {}
    path = Path(path)
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    cfg = ExperimentConfig()
    return ExperimentConfig(
        endpoint=env.get("PINNED_AOAI_ENDPOINT", data.get("endpoint", cfg.endpoint)),
        api_version=env.get("PINNED_AOAI_API_VERSION", data.get("api_version", cfg.api_version)),
        strong=env.get("PINNED_AOAI_STRONG", data.get("strong", cfg.strong)),
        small=env.get("PINNED_AOAI_SMALL", data.get("small", cfg.small)),
        temperature=float(data.get("temperature", cfg.temperature)),
        max_retries=int(data.get("max_retries", cfg.max_retries)),
    )
