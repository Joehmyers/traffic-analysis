"""Build WebTRIS daily totals for every site, newest month first, resuming after any interruption.

Usage: python extract_daily.py [--db data/webtris-daily/webtris_daily.sqlite] [--start 2018-01]
                               [--end YYYY-MM] [--export out.csv]

Phase 1 fetches each site's daily quality once and records which months hold data.
Phase 2 fetches the 15-minute rows for each site-month with data, newest month first, sums
them to one row per site-day, and writes the rows and a "done" mark in one SQLite transaction.
Closing the laptop or killing the process loses at most the requests in flight: on the next run
the done marks show where to start. Re-running is safe at any point.

Standard library only.
"""
import argparse
import concurrent.futures as cf
import csv
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path

from extract_sample import PAGE_SIZE, get, month_bounds

MAX_IN_FLIGHT = 2
MAX_CONSECUTIVE_FAILURES = 10
BANDS = ("0 - 520 cm", "521 - 660 cm", "661 - 1160 cm", "1160+ cm")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (site_id TEXT PRIMARY KEY, name TEXT, description TEXT,
    status TEXT, latitude REAL, longitude REAL);
CREATE TABLE IF NOT EXISTS quality_done (site_id TEXT PRIMARY KEY, loaded_at TEXT);
CREATE TABLE IF NOT EXISTS site_months (site_id TEXT, ym TEXT, done INTEGER DEFAULT 0,
    PRIMARY KEY (site_id, ym));
CREATE TABLE IF NOT EXISTS daily_totals (site_id TEXT, date TEXT, total_volume INTEGER,
    slots_present INTEGER, rows_returned INTEGER, vol_0_520 INTEGER, vol_521_660 INTEGER,
    vol_661_1160 INTEGER, vol_1160_plus INTEGER, avg_mph REAL,
    PRIMARY KEY (site_id, date));
CREATE TABLE IF NOT EXISTS load_log (request TEXT, attempt INTEGER, http_status INTEGER,
    duration_ms INTEGER, error TEXT, requested_at TEXT);
CREATE INDEX IF NOT EXISTS idx_site_months_todo ON site_months (done, ym);
"""


def to_int(value):
    return int(value) if value not in ("", None) else None


def summarise_days(rows):
    """Sum 15-minute report rows into one dict per date.

    An empty Total Volume is a missing slot and is left out of every sum, so the blank twin of
    the repeated hour on a clocks-back day drops out. slots_present counts the slots with a value.
    avg_mph is weighted by slot volume.
    """
    days = {}
    for row in rows:
        day = days.setdefault(row["Report Date"][:10], {
            "rows_returned": 0, "slots_present": 0, "total_volume": 0,
            "bands": [0, 0, 0, 0], "speed_x_volume": 0.0, "speed_volume": 0})
        day["rows_returned"] += 1
        volume = to_int(row["Total Volume"])
        if volume is None:
            continue
        day["slots_present"] += 1
        day["total_volume"] += volume
        for i, band in enumerate(BANDS):
            day["bands"][i] += to_int(row[band]) or 0
        mph = row.get("Avg mph")
        if mph not in ("", None) and volume:
            day["speed_x_volume"] += float(mph) * volume
            day["speed_volume"] += volume
    out = {}
    for d, day in days.items():
        out[d] = {
            "total_volume": day["total_volume"] if day["slots_present"] else None,
            "slots_present": day["slots_present"], "rows_returned": day["rows_returned"],
            "vol_0_520": day["bands"][0], "vol_521_660": day["bands"][1],
            "vol_661_1160": day["bands"][2], "vol_1160_plus": day["bands"][3],
            "avg_mph": round(day["speed_x_volume"] / day["speed_volume"], 2)
            if day["speed_volume"] else None,
        }
    return out


def now():
    return datetime.now(timezone.utc).isoformat()


def log_rows(db, log):
    db.executemany("INSERT INTO load_log VALUES (?,?,?,?,?,?)",
                   [(e["request"], e["attempt"], e["http_status"], e["duration_ms"], e["error"],
                     e["requested_at"]) for e in log])


def work_quality(site_id, start, end):
    log = []
    body = get(f"quality/daily?siteId={site_id}&start_date={start}&end_date={end}",
               log, f"quality/daily site={site_id}") or {"Qualities": []}
    months = sorted({q["Date"][:7] for q in body.get("Qualities", []) if q["Quality"] > 0})
    return months, log


def work_flows(site_id, ym):
    log = []
    start, end = month_bounds(ym)
    body = get(f"reports/daily?sites={site_id}&start_date={start}&end_date={end}"
               f"&page=1&page_size={PAGE_SIZE}&report_type=daily",
               log, f"reports/daily site={site_id} {ym}")
    rows = body["Rows"] if body else []
    if len(rows) >= PAGE_SIZE:
        raise RuntimeError(f"site {site_id} {ym}: a full page came back; paging is needed")
    return summarise_days(rows), log


def run_queue(db, label, todo, submit, record, total_done, total):
    """Run tasks 2 at a time. Each result is committed before the next is taken.

    A task that fails is logged and left undone for the next run. Ten failures in a row
    (for example the laptop was asleep or offline) stop the run.
    """
    started, finished, consecutive = time.monotonic(), 0, 0
    todo = iter(todo)
    with cf.ThreadPoolExecutor(MAX_IN_FLIGHT) as pool:
        pending = {}
        while True:
            while len(pending) < MAX_IN_FLIGHT and consecutive < MAX_CONSECUTIVE_FAILURES:
                item = next(todo, None)
                if item is None:
                    break
                pending[pool.submit(submit, *item)] = item
            if not pending:
                break
            done, _ = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for future in done:
                item = pending.pop(future)
                try:
                    result, log = future.result()
                except RuntimeError as exc:
                    consecutive += 1
                    print(f"FAILED {item}: {exc}", flush=True)
                    continue
                consecutive = 0
                with db:
                    log_rows(db, log)
                    record(item, result)
                finished += 1
                if finished % 50 == 0:
                    rate = finished / (time.monotonic() - started)
                    left = (total - total_done - finished) / rate / 3600
                    print(f"{label}: {total_done + finished}/{total} done, "
                          f"about {left:.1f} h left at this run's pace", flush=True)
    if consecutive >= MAX_CONSECUTIVE_FAILURES:
        raise SystemExit(f"{consecutive} failures in a row. Check the network and run again to resume.")


def load_sites(db):
    if db.execute("SELECT 1 FROM sites LIMIT 1").fetchone():
        return
    print("Fetching site list", flush=True)
    log = []
    sites = get("sites", log, "sites")["sites"]
    with db:
        db.executemany("INSERT OR IGNORE INTO sites VALUES (?,?,?,?,?,?)",
                       [(s["Id"], s["Name"], s["Description"], s["Status"], s["Latitude"],
                         s["Longitude"]) for s in sites])
        log_rows(db, log)


def phase_quality(db, start, end):
    todo = [(r[0],) for r in db.execute(
        "SELECT site_id FROM sites WHERE site_id NOT IN (SELECT site_id FROM quality_done) "
        "ORDER BY status, CAST(site_id AS INTEGER)")]
    total = db.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
    print(f"Phase 1: quality for {len(todo)} of {total} sites", flush=True)

    def record(item, months):
        site_id = item[0]
        db.executemany("INSERT OR IGNORE INTO site_months (site_id, ym) VALUES (?, ?)",
                       [(site_id, m) for m in months])
        db.execute("INSERT INTO quality_done VALUES (?, ?)", (site_id, now()))

    run_queue(db, "quality", todo, lambda s: work_quality(s, start, end), record,
              total - len(todo), total)


def phase_flows(db, first_ym, last_ym):
    todo = [tuple(r) for r in db.execute(
        "SELECT site_id, ym FROM site_months WHERE done = 0 AND ym BETWEEN ? AND ? "
        "ORDER BY ym DESC, CAST(site_id AS INTEGER)", (first_ym, last_ym))]
    total = db.execute("SELECT COUNT(*) FROM site_months WHERE ym BETWEEN ? AND ?",
                       (first_ym, last_ym)).fetchone()[0]
    print(f"Phase 2: {len(todo)} of {total} site-months to fetch"
          + (f", newest {todo[0][1]}" if todo else ""), flush=True)

    def record(item, days):
        site_id, ym = item
        db.executemany(
            "INSERT OR REPLACE INTO daily_totals VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(site_id, d, v["total_volume"], v["slots_present"], v["rows_returned"],
              v["vol_0_520"], v["vol_521_660"], v["vol_661_1160"], v["vol_1160_plus"],
              v["avg_mph"]) for d, v in days.items()])
        db.execute("UPDATE site_months SET done = 1 WHERE site_id = ? AND ym = ?", (site_id, ym))

    run_queue(db, "site-months", todo, work_flows, record, total - len(todo), total)


def export_csv(db, path):
    cursor = db.execute("SELECT * FROM daily_totals ORDER BY date DESC, CAST(site_id AS INTEGER)")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([c[0] for c in cursor.description])
        writer.writerows(cursor)
    print(f"Exported to {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, default=Path("data/webtris-daily/webtris_daily.sqlite"))
    parser.add_argument("--start", default="2018-01")
    parser.add_argument("--end", default=date.today().strftime("%Y-%m"))
    parser.add_argument("--max-sites", type=int, help="trial run: keep only the first N site IDs")
    parser.add_argument("--export", type=Path, help="write daily_totals to this CSV and exit")
    args = parser.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.db)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    if args.export:
        export_csv(db, args.export)
        return
    load_sites(db)
    if args.max_sites:
        db.execute("DELETE FROM sites WHERE site_id NOT IN (SELECT site_id FROM sites "
                   "WHERE status = 'Active' ORDER BY CAST(site_id AS INTEGER) LIMIT ?)",
                   (args.max_sites,))
        db.commit()
    first_day, _ = month_bounds(args.start)
    _, last_day = month_bounds(args.end)
    phase_quality(db, first_day, last_day)
    phase_flows(db, args.start, args.end)
    print("DONE")


if __name__ == "__main__":
    main()
