#!/usr/bin/env python3
"""작업 디렉터리를 PPTX zip 패키지로 다시 묶는다."""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("unpacked_dir", type=Path)
    parser.add_argument("output_pptx", type=Path)
    parser.add_argument("--original", type=Path, default=None)
    parser.add_argument("--validate", default="true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.unpacked_dir.is_dir():
        print(f"unpacked dir not found: {args.unpacked_dir}", file=sys.stderr)
        return 2

    args.output_pptx.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output_pptx, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(args.unpacked_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(args.unpacked_dir).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
