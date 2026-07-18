#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import mimetypes
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replace the self-contained collage in an existing card audit HTML."
    )
    parser.add_argument("html", type=Path)
    parser.add_argument("collage", type=Path)
    args = parser.parse_args()

    media_type = mimetypes.guess_type(args.collage.name)[0] or "image/jpeg"
    data_uri = (
        f"data:{media_type};base64,"
        + base64.b64encode(args.collage.read_bytes()).decode("ascii")
    )
    html = args.html.read_text(encoding="utf-8")
    pattern = re.compile(
        r'(<img\b[^>]*\bclass="[^"]*\bcollage\b[^"]*"[^>]*\bsrc=")'
        r'data:image/[^"]+(")',
        flags=re.I,
    )
    updated, replacements = pattern.subn(
        lambda match: f"{match.group(1)}{data_uri}{match.group(2)}",
        html,
        count=1,
    )
    if replacements != 1:
        raise SystemExit(
            f"Expected one embedded collage in {args.html}, found {replacements}"
        )
    args.html.write_text(updated, encoding="utf-8")
    print(args.html)


if __name__ == "__main__":
    main()
