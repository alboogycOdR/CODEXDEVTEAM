"""Read-only CLI for scoring fast-tier predictions against a labeled corpus."""

import argparse
import json
import sys
from pathlib import Path

from .fast_tier_eval import evaluate_run_log_corpus


def _read_jsonl(path: Path, label: str) -> list[dict[str, object]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} path must name a regular file")
    rows: list[dict[str, object]] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{label} line {line_number} must be an object")
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {label} JSONL") from exc
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        metrics = evaluate_run_log_corpus(
            _read_jsonl(args.cases, "corpus"),
            _read_jsonl(args.predictions, "predictions"),
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(metrics, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
