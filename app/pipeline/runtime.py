"""Lazy runtime setup allows Functions indexing while production processing is disabled."""

from functools import lru_cache
import json
import os
from pathlib import Path

from .engine import Engine
from .contracts import validate_v2
from .intelligence import Intelligence
from .policy import Rejected, validate_config
from .storage import Store, credential


def processing_enabled():
    return os.environ.get("PROCESSING_ENABLED", "false").lower() == "true"


def limits():
    upload = int(os.environ.get("MAX_UPLOAD_BYTES", "20971520"))
    pages = int(os.environ.get("MAX_PAGES", "100"))
    if not 1 <= upload <= 20971520 or not 1 <= pages <= 100:
        raise Rejected("invalid_resource_limits")
    return upload, pages


@lru_cache(maxsize=1)
def services():
    identity = credential()
    store = Store(identity)
    ai = Intelligence(os.environ["DOCUMENT_INTELLIGENCE_ENDPOINT"], identity)
    return store, Engine(store, ai, limits()[1])


@lru_cache(maxsize=1)
def admission_config():
    config = validate_v2(validate_config(json.loads(
        Path(__file__).with_name("config.json").read_text(encoding="utf-8"))))
    # A live read is mandatory when admitting new work, not just a CLI recommendation.
    # Each job snapshots the vetted config so deployments do not silently change retries.
    services()[1].ai.preflight(config)
    return config
