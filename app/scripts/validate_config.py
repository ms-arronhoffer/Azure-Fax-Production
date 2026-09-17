"""Validate production policy, optionally against real Document Intelligence metadata."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.policy import validate_config  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "pipeline" / "config.json")
    parser.add_argument("--live", action="store_true", help="Require actual classifier classes and model field schemas.")
    args = parser.parse_args()
    try:
        config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
        if args.live:
            from pipeline.intelligence import Intelligence
            from pipeline.storage import credential
            Intelligence(os.environ["DOCUMENT_INTELLIGENCE_ENDPOINT"], credential()).preflight(config)
        print("PASS: static policy" + (" and live model schemas" if args.live else
                                     " only; live verification still required before enabling"))
        return 0
    except Exception as error:
        # Schema/service errors may contain endpoint/model details. Keep output safe.
        print(f"FAIL: configuration unavailable or invalid ({type(error).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
