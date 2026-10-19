from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    relay_interval_seconds: float
    relay_batch_size: int


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///./ledgerline.db"),
        relay_interval_seconds=float(os.environ.get("RELAY_INTERVAL_SECONDS", "1.0")),
        relay_batch_size=int(os.environ.get("RELAY_BATCH_SIZE", "100")),
    )
