from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
OUT_ROOT = ROOT / "BackgroundVideos"

TARGETS = {
    "cooking": [
        "cooking",
        "food-preparation",
        "food",
        "kitchen",
        "cook",
        "chef",
        "vegetable",
        "breakfast",
        "dessert",
        "salad",
        "meat",
        "bread",
        "restaurant",
        "coffee",
        "fruit",
        "drink",
    ],
    "street": ["street", "city", "traffic", "road", "people", "walk", "urban"],
    "rain": ["rain", "water", "cloud", "sky", "night", "storm"],
    "house": ["house", "family", "home", "room", "kitchen", "lifestyle"],
    "office": ["business", "office", "computer", "laptop", "work", "technology"],
    "driving": ["car", "road", "traffic", "travel", "city", "highway"],
    "nature": ["nature", "forest", "sunset", "sea", "sky", "flower", "water"],
    "wedding": ["wedding", "love", "couple", "party", "woman", "family"],
}


def http_get(url: str, timeout: int = 30) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        return response.read()


def url_exists(url: str, timeout: int = 12) -> bool:
    req = Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=timeout) as response:
            return 200 <= int(response.status) < 300
    except Exception:
        return False


def preferred_url(url: str) -> str:
    for suffix in ("2160", "1440", "1080"):
        candidate = re.sub(rf"-{suffix}\.mp4$", "-720.mp4", url)
        if candidate != url and url_exists(candidate):
            return candidate
    return url


def extract_video_urls(html: str) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, flags=re.S):
        try:
            data = json.loads(raw)
        except Exception:
            continue
        graph = data.get("@graph") if isinstance(data, dict) else None
        if not isinstance(graph, list):
            continue
        for item in graph:
            if not isinstance(item, dict) or item.get("@type") != "VideoObject":
                continue
            url = str(item.get("contentUrl") or "").strip()
            if not url or not url.endswith(".mp4") or url in seen:
                continue
            seen.add(url)
            name = clean_name(str(item.get("name") or Path(url).stem))
            urls.append((name, preferred_url(url)))
    return urls


def clean_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return text[:60] or "mixkit_video"


def fetch_category_urls(slug: str, page: int = 1) -> list[tuple[str, str]]:
    suffix = "" if page <= 1 else f"?page={int(page)}"
    url = f"https://mixkit.co/free-stock-video/{slug}/{suffix}"
    html = http_get(url).decode("utf-8", errors="ignore")
    return extract_video_urls(html)


def download_file(url: str, output: Path, timeout: int = 180) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".part")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as response:
        with tmp.open("wb") as file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
    tmp.replace(output)


def existing_count(folder: Path) -> int:
    return len([p for p in folder.glob("*.mp4") if p.is_file()])


def existing_total_count(root: Path) -> int:
    return len([p for p in root.rglob("*.mp4") if p.is_file()])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, default=120)
    parser.add_argument("--per-folder", type=int, default=15)
    parser.add_argument("--pages", type=int, default=8)
    parser.add_argument("--folders", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    existing_total = existing_total_count(OUT_ROOT)
    remaining_total = max(0, int(args.total) - existing_total)
    if remaining_total <= 0:
        print(f"existing={existing_total}")
        print("planned=0")
        return 0

    global_seen: set[str] = set()
    planned: list[tuple[str, str, str]] = []

    selected_folders = {item.strip().lower() for item in str(args.folders or "").split(",") if item.strip()}

    for folder_name, slugs in TARGETS.items():
        if selected_folders and folder_name.lower() not in selected_folders:
            continue
        folder = OUT_ROOT / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        wanted = max(0, int(args.per_folder) - existing_count(folder))
        if wanted <= 0:
            continue
        for slug in slugs:
            for page in range(1, max(1, int(args.pages)) + 1):
                try:
                    items = fetch_category_urls(slug, page=page)
                except (HTTPError, URLError, TimeoutError) as exc:
                    print(f"[skip-page] {slug} page {page}: {exc}")
                    continue
                if not items:
                    break
                for title, url in items:
                    if url in global_seen:
                        continue
                    global_seen.add(url)
                    planned.append((folder_name, title, url))
                    wanted -= 1
                    if len(planned) >= remaining_total or wanted <= 0:
                        break
                if len(planned) >= remaining_total or wanted <= 0:
                    break
                time.sleep(0.2)
            if len(planned) >= remaining_total or wanted <= 0:
                break
        if len(planned) >= remaining_total:
            break

    print(f"existing={existing_total}")
    print(f"planned={len(planned)}")
    for index, (folder_name, title, url) in enumerate(planned, start=1):
        print(f"{index:03d} {folder_name}: {title} -> {url}")

    if args.dry_run:
        return 0

    for index, (folder_name, title, url) in enumerate(planned, start=1):
        output = OUT_ROOT / folder_name / f"{folder_name}_{index:03d}_{title}.mp4"
        if output.exists() and output.stat().st_size > 0:
            print(f"[exists] {output}")
            continue
        print(f"[download {index}/{len(planned)}] {output.name}")
        try:
            download_file(url, output)
        except Exception as exc:
            print(f"[failed] {url}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
