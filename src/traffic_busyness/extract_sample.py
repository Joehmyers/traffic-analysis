"""Download a WebTRIS sample: 15-minute counts for N random active sites, one month per request.

Usage: python extract_sample.py [--sites 50] [--start 2026-01] [--end 2026-06] [--out assets/webtris-sample]

Standard library only. Re-running skips files that already exist, so an interrupted run resumes.
"""
import argparse
import calendar
import collections
import concurrent.futures
import json
import random
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://webtris.nationalhighways.co.uk/api/v1.0"
MAX_IN_FLIGHT = 2
PAGE_SIZE = 40000
RETRIES = 3
SITE_TYPES = ("MIDAS", "TMU", "TAME")


def get(path, log, label):
    """GET a JSON path. Returns the parsed body, or None for HTTP 204 (no data)."""
    url = f"{BASE}/{path}"
    for attempt in range(RETRIES + 1):
        started = time.monotonic()
        status, body, error = None, None, None
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                status, raw = resp.status, resp.read()
            body = json.loads(raw) if status != 204 and raw else None
        except urllib.error.HTTPError as exc:
            status, error = exc.code, f"{exc.code} {exc.read()[:200]!r}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            error = str(exc)
        log.append({
            "request": label, "attempt": attempt, "http_status": status,
            "duration_ms": int((time.monotonic() - started) * 1000), "error": error,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        })
        if error is None:
            return body
        retryable = status is None or status >= 500
        if not retryable or attempt == RETRIES:
            raise RuntimeError(f"{label}: {error}")
        time.sleep(2 ** (attempt + 1))


def month_bounds(ym):
    year, month = map(int, ym.split("-"))
    last = calendar.monthrange(year, month)[1]
    return f"01{month:02d}{year}", f"{last:02d}{month:02d}{year}"


def months_between(start, end):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    while (y, m) <= (ey, em):
        yield f"{y}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def pick_sites(sites, count, seed):
    """Random active MIDAS, TMU or TAME sites whose name no other site shares.

    Report rows carry only the site name, so a shared name would make a row ambiguous.
    """
    name_counts = collections.Counter(s["Name"] for s in sites)
    pool = [
        s for s in sites
        if s["Status"] == "Active" and name_counts[s["Name"]] == 1
        and re.match(rf"({'|'.join(SITE_TYPES)})", s.get("Description") or "")
    ]
    pool.sort(key=lambda s: int(s["Id"]))
    return random.Random(seed).sample(pool, count)


def fetch_site_month(site_id, ym, out_dir, log):
    path = out_dir / "flows" / site_id / f"{ym}.json"
    if path.exists():
        return ym, "cached"
    start, end = month_bounds(ym)
    label = f"reports/daily site={site_id} {ym}"
    query = (f"reports/daily?sites={site_id}&start_date={start}&end_date={end}"
             f"&page=1&page_size={PAGE_SIZE}&report_type=daily")
    body = get(query, log, label)
    if body is None:
        return ym, "no data"
    if body["Header"]["row_count"] > PAGE_SIZE:
        raise RuntimeError(f"{label}: more than one page; add paging")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return ym, f"{body['Header']['row_count']} rows"


def fetch_site(site, months, out_dir):
    log = []
    site_id = site["Id"]
    start, _ = month_bounds(months[0])
    _, end = month_bounds(months[-1])
    quality = get(f"quality/daily?siteId={site_id}&start_date={start}&end_date={end}",
                  log, f"quality/daily site={site_id}") or {"Qualities": []}
    (out_dir / "quality").mkdir(parents=True, exist_ok=True)
    (out_dir / "quality" / f"{site_id}.json").write_text(json.dumps(quality), encoding="utf-8")
    # Keep only months with data on at least one day. Dates arrive as ISO strings.
    live = {q["Date"][:7] for q in quality.get("Qualities", []) if q["Quality"] > 0}
    results = {}
    for ym in months:
        if ym in live:
            results[ym] = fetch_site_month(site_id, ym, out_dir, log)
    return site_id, results, log


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sites", type=int, default=50)
    parser.add_argument("--start", default="2026-01")
    parser.add_argument("--end", default="2026-06")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("assets/webtris-sample"))
    args = parser.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    months = list(months_between(args.start, args.end))
    boot_log = []
    all_sites = get("sites", boot_log, "sites")["sites"]
    chosen = pick_sites(all_sites, args.sites, args.seed)
    (out_dir / "sites.json").write_text(json.dumps({"sites": chosen}, indent=1), encoding="utf-8")
    print(f"{len(chosen)} sites, {len(months)} months, writing to {out_dir}")

    failures = 0
    with open(out_dir / "load_log.jsonl", "a", encoding="utf-8") as log_file, \
            concurrent.futures.ThreadPoolExecutor(MAX_IN_FLIGHT) as pool:
        for entry in boot_log:
            log_file.write(json.dumps(entry) + "\n")
        futures = {pool.submit(fetch_site, s, months, out_dir): s for s in chosen}
        for future in concurrent.futures.as_completed(futures):
            site = futures[future]
            try:
                site_id, results, log = future.result()
            except RuntimeError as exc:
                failures += 1
                print(f"FAILED {site['Id']} {site['Name']}: {exc}")
                continue
            for entry in log:
                log_file.write(json.dumps({"site_id": site_id, **entry}) + "\n")
            log_file.flush()
            print(f"{site_id} {site['Name']}: " + ", ".join(f"{m} {r[1]}" for m, r in results.items()))
    if failures > len(chosen) * 0.05:
        raise SystemExit(f"{failures} of {len(chosen)} sites failed (over 5%)")
    print("DONE")


if __name__ == "__main__":
    main()
