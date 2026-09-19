#!/usr/bin/env python3
"""下載信用卡圖片、移除外圍空白，並輸出網站使用的 WebP 資產。"""

from __future__ import annotations

import argparse
import io
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageOps


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "src/data/creditCardArtwork.ts"
OUTPUT_PATH = ROOT / "public/card-art"
USER_AGENT = "Mozilla/5.0 (compatible; MeichuCardArtwork/1.0)"
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
BACKGROUND_THRESHOLD = 24
PADDING_RATIO = 0.02
MIN_FOREGROUND_AREA_RATIO = 0.05


@dataclass(frozen=True)
class CardArtwork:
    id: str
    url: str


def parse_catalog(path: Path) -> list[CardArtwork]:
    entries: list[CardArtwork] = []
    seen_ids: set[str] = set()

    for block in path.read_text(encoding="utf-8").split("\n  {"):
        card_id = re.search(r"\bid:\s*'([^']+)'", block)
        image_url = re.search(r"\bofficial_image_url:\s*(?:\n\s*)?'([^']+)'", block)
        if not card_id or not image_url or card_id.group(1) in seen_ids:
            continue

        if not re.fullmatch(r"[0-9A-Za-z._-]+", card_id.group(1)):
            raise ValueError(f"不安全的圖片識別碼：{card_id.group(1)}")

        url = image_url.group(1)
        if urlparse(url).scheme not in {"http", "https"}:
            raise ValueError(f"不支援的圖片網址：{url}")

        seen_ids.add(card_id.group(1))
        entries.append(CardArtwork(card_id.group(1), url))

    if not entries:
        raise ValueError(f"找不到信用卡圖片資料：{path}")

    return entries


def download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "image/avif,image/webp,image/*,*/*;q=0.8", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
            raise ValueError("圖片超過 25 MiB 上限")

        payload = response.read(MAX_DOWNLOAD_BYTES + 1)
        if len(payload) > MAX_DOWNLOAD_BYTES:
            raise ValueError("圖片超過 25 MiB 上限")
        return payload


def is_similar(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    return max(abs(a - b) for a, b in zip(left[:3], right[:3], strict=True)) <= BACKGROUND_THRESHOLD


def has_neutral_edge(color: tuple[int, ...]) -> bool:
    return max(color[:3]) - min(color[:3]) <= BACKGROUND_THRESHOLD


def transparent_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    alpha = image.getchannel("A")
    mask = alpha.point(lambda value: 255 if value > 8 else 0)
    return mask.getbbox()


def remove_connected_background(image: Image.Image) -> Image.Image:
    width, height = image.size
    ratio = width / height
    alpha = image.getchannel("A")

    if alpha.getextrema()[0] < 250:
        bbox = transparent_bbox(image)
        return image.crop(bbox) if bbox else image

    # 原生橫式或直式卡面通常已貼齊畫布，不以角落顏色推測背景。
    if 1.4 <= ratio <= 1.75 or 0.55 <= ratio <= 0.75:
        return image

    corners = [
        ((0, 0), image.getpixel((0, 0))),
        ((width - 1, 0), image.getpixel((width - 1, 0))),
        ((0, height - 1), image.getpixel((0, height - 1))),
        ((width - 1, height - 1), image.getpixel((width - 1, height - 1))),
    ]
    candidates = [
        point
        for point, color in corners
        if has_neutral_edge(color)
        and sum(is_similar(color, other_color) for _, other_color in corners) >= 2
    ]
    if not candidates:
        return image

    marker = (1, 2, 3, 0)
    trimmed = image.copy()
    for point in candidates:
        if trimmed.getpixel(point) != marker:
            ImageDraw.floodfill(trimmed, point, marker, thresh=BACKGROUND_THRESHOLD)

    bbox = transparent_bbox(trimmed)
    if not bbox:
        return image

    cropped_width = bbox[2] - bbox[0]
    cropped_height = bbox[3] - bbox[1]
    if cropped_width * cropped_height < width * height * MIN_FOREGROUND_AREA_RATIO:
        return image

    return trimmed.crop(bbox)


def crop_artwork(source: Image.Image) -> Image.Image:
    source.seek(0)
    image = ImageOps.exif_transpose(source).convert("RGBA")
    cropped = remove_connected_background(image)
    padding = max(2, round(max(cropped.size) * PADDING_RATIO))
    output = Image.new("RGBA", (cropped.width + padding * 2, cropped.height + padding * 2))
    output.alpha_composite(cropped, (padding, padding))
    return output


def process(entry: CardArtwork, output_dir: Path, force: bool) -> tuple[str, str]:
    output = output_dir / f"{entry.id}.webp"
    if output.exists() and not force:
        with Image.open(output) as existing:
            existing.verify()
        return entry.id, "略過"

    payload = download(entry.url)
    with Image.open(io.BytesIO(payload)) as source:
        artwork = crop_artwork(source)
        artwork.save(output, "WEBP", quality=92, method=6)
    return entry.id, "完成"


def self_test() -> None:
    landscape = Image.new("RGB", (520, 520), "white")
    ImageDraw.Draw(landscape).rectangle((4, 92, 518, 415), fill="black")
    landscape_result = crop_artwork(landscape)
    assert 1.45 < landscape_result.width / landscape_result.height < 1.65

    portrait = Image.new("RGBA", (400, 400))
    ImageDraw.Draw(portrait).rectangle((85, 18, 314, 382), fill="red")
    portrait_result = crop_artwork(portrait)
    assert portrait_result.width / portrait_result.height < 0.75


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    self_test()
    if args.self_test:
        print("圖片裁切自我檢查通過")
        return 0

    entries = parse_catalog(args.catalog)
    args.output.mkdir(parents=True, exist_ok=True)
    failures: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(process, entry, args.output, args.force): entry for entry in entries
        }
        for future in as_completed(futures):
            entry = futures[future]
            try:
                card_id, status = future.result()
                print(f"[{status}] {card_id}")
            except Exception as error:
                failures.append((entry.id, str(error)))
                print(f"[失敗] {entry.id}：{error}", file=sys.stderr)

    if failures:
        print(f"{len(failures)} 張圖片處理失敗", file=sys.stderr)
        return 1

    print(f"共處理 {len(entries)} 張圖片")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
