#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


THUMB_SIZE = (420, 560)
LABEL_HEIGHT = 42
COLS = 3
GAP = 16
BACKGROUND = (245, 246, 248)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def render_with_chromium(items: list[tuple[str, Path]]) -> None:
    script = r"""
const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const items = JSON.parse(fs.readFileSync(0, 'utf8'));
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 2200, height: 2800 },
    deviceScaleFactor: 1
  });
  for (const item of items) {
    const sourceUrl = item.source;
    await page.setContent(
      `<style>html,body{margin:0;background:white}img{display:block;max-width:none}</style>` +
      `<img id="asset" src="${sourceUrl}">`
    );
    await page.waitForFunction(() => {
      const image = document.querySelector('#asset');
      return image && image.complete && image.naturalWidth > 0;
    });
    await page.locator('#asset').screenshot({ path: item.target, type: 'png' });
  }
  await browser.close();
})().catch(error => {
  console.error(error);
  process.exit(1);
});
"""
    env = os.environ.copy()
    env["NODE_PATH"] = "/home/Codex/agent-tools/node/node_modules"
    payload = [
        {"source": source, "target": str(target.resolve())}
        for source, target in items
    ]
    proc = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(payload),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Chromium image rendering failed")


def build_collage(package_path: Path, output_path: Path) -> dict:
    package = load_json(package_path)
    assets = package.get("media_assets") or []
    if not assets:
        raise SystemExit(f"No media_assets in {package_path}")

    normalized: list[tuple[str, Image.Image]] = []
    source_color_spaces: list[dict[str, str | int]] = []
    with tempfile.TemporaryDirectory(prefix="card_srgb_") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        render_items: list[tuple[str, Path]] = []
        downloaded: list[tuple[dict, Path, Path]] = []
        for index, asset in enumerate(assets, start=1):
            marketplace = str(asset["marketplace"]).lower()
            position = int(asset["position"])
            suffix = Path(str(asset["url"])).suffix or ".jpg"
            source = temp_dir / f"{index:02d}_{marketplace}_{position}{suffix}"
            target = temp_dir / f"{index:02d}_{marketplace}_{position}_srgb.png"
            urllib.request.urlretrieve(asset["url"], source)
            identify = subprocess.run(
                ["identify", "-format", "%[colorspace]", str(source)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            render_items.append((str(asset["url"]), target))
            downloaded.append((asset, source, target))
            source_color_spaces.append(
                {
                    "marketplace": marketplace,
                    "position": position,
                    "source_colorspace": identify,
                    "normalized_colorspace": "sRGB",
                }
            )

        render_with_chromium(render_items)
        for asset, _source, target in downloaded:
            marketplace = str(asset["marketplace"]).lower()
            position = int(asset["position"])
            with Image.open(target) as image:
                normalized.append(
                    (
                        f"{marketplace.upper()} {position}",
                        image.convert("RGB").copy(),
                    )
                )

    rows = (len(normalized) + COLS - 1) // COLS
    tile_width = THUMB_SIZE[0]
    tile_height = THUMB_SIZE[1] + LABEL_HEIGHT
    collage = Image.new(
        "RGB",
        (
            GAP + COLS * (tile_width + GAP),
            GAP + rows * (tile_height + GAP),
        ),
        BACKGROUND,
    )
    draw = ImageDraw.Draw(collage)
    font = ImageFont.load_default()

    for index, (label, image) in enumerate(normalized):
        row, col = divmod(index, COLS)
        x = GAP + col * (tile_width + GAP)
        y = GAP + row * (tile_height + GAP)
        tile = ImageOps.contain(image, THUMB_SIZE)
        image_x = x + (tile_width - tile.width) // 2
        image_y = y + (THUMB_SIZE[1] - tile.height) // 2
        collage.paste(tile, (image_x, image_y))
        draw.rectangle(
            (x, y + THUMB_SIZE[1], x + tile_width, y + tile_height),
            fill=(255, 255, 255),
        )
        draw.text(
            (x + 12, y + THUMB_SIZE[1] + 13),
            label,
            fill=(20, 24, 32),
            font=font,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    collage.save(output_path, "JPEG", quality=88, optimize=True)
    return {
        "asset_count": len(assets),
        "output": str(output_path),
        "source_color_spaces": source_color_spaces,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a correctly color-managed sRGB collage from Layer 1 media assets."
    )
    parser.add_argument("package_json", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = build_collage(args.package_json, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
