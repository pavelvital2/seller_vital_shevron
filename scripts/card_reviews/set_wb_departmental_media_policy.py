from __future__ import annotations

import argparse
import json
from pathlib import Path


RULE = (
    "WB images with visible state or departmental symbols must be retouched or "
    "covered by a watermark. Neutral service and wearing images without symbols "
    "may be used without a watermark."
)


def _positions(value: str) -> list[int]:
    return [int(item) for item in value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record a manually verified WB departmental media policy in Layer 2 or Layer 3 JSON."
    )
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--status",
        required=True,
        choices=("allowed_verified", "blocked_pending_watermarked_assets", "not_applicable"),
    )
    parser.add_argument("--protected", default="")
    parser.add_argument("--neutral", default="")
    parser.add_argument("--unprotected", default="")
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    payload = json.loads(args.path.read_text(encoding="utf-8"))
    media = payload.setdefault("media", {})
    media["wb_departmental_symbol_policy"] = {
        "checked_at": "2026-07-16",
        "checked_by": "codex_visual_review",
        "media_apply_status": args.status,
        "rule": RULE,
        "protected_symbol_positions": _positions(args.protected),
        "neutral_no_symbol_positions": _positions(args.neutral),
        "unprotected_symbol_positions": _positions(args.unprotected),
        "note": args.note,
    }
    args.path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
