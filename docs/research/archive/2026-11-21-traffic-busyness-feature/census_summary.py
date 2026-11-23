"""Summarise the WebTRIS quality census written by quality_census.py.

Usage: python3 -I census_summary.py quality_census.json

Prints, by site type and status, the share of sampled sites that are usable each
year (at least 80% of days with quality of 90% or more), the share with no data in
a year, and the spread of daily quality values."""
import json, sys, collections, statistics
d = json.load(open(sys.argv[1]))
errs = [k for k, v in d.items() if v["error"]]
print("sites", len(d), "errors", len(errs))
years = range(2018, 2027)
def share(q, year, thr):
    days = [v for k, v in q.items() if k.startswith(str(year))]
    return sum(1 for v in days if v is not None and v >= thr) / len(days) if days else float("nan")
groups = collections.defaultdict(list)
for sid, v in d.items(): groups[(v["type"], v["status"])].append(v)
print("\nShare of site-days with a near-complete day (quality >= 90), mean over sampled sites")
print("group".ljust(22), " ".join(str(y) for y in years))
for g, vs in sorted(groups.items()):
    row = [statistics.mean(share(v["q"], y, 90) for v in vs) for y in years]
    print(f"{g[0]+'/'+g[1]:<22}", " ".join(f"{x:4.0%}" for x in row), f"(n={len(vs)})")
print("\nShare of sampled sites that are 'usable' in a year (>= 80% of days with quality >= 90)")
for g, vs in sorted(groups.items()):
    row = [sum(1 for v in vs if share(v["q"], y, 90) >= 0.8) / len(vs) for y in years]
    print(f"{g[0]+'/'+g[1]:<22}", " ".join(f"{x:4.0%}" for x in row))
print("\nShare of sampled sites with no data at all (quality 0 every day) in the year")
for g, vs in sorted(groups.items()):
    row = [sum(1 for v in vs if share(v["q"], y, 1) == 0) / len(vs) for y in years]
    print(f"{g[0]+'/'+g[1]:<22}", " ".join(f"{x:4.0%}" for x in row))
# distribution of quality values among days with some data
vals = collections.Counter()
for v in d.values():
    for x in v["q"].values():
        if x is None: vals["null"] += 1
        elif x == 0: vals["0"] += 1
        elif x == 100: vals["100"] += 1
        elif x >= 90: vals["90-99"] += 1
        elif x >= 50: vals["50-89"] += 1
        else: vals["1-49"] += 1
tot = sum(vals.values())
print("\nQuality value distribution over all site-days:", {k: f"{n/tot:.1%}" for k, n in vals.most_common()})
# Last date with data per active site
print("\nLast date with quality > 0, active sites:")
last = collections.Counter()
for v in d.values():
    if v["status"] != "Active": continue
    good = [k for k, x in v["q"].items() if x and x > 0]
    last[(max(good)[:7] if good else "never")] += 1
print(sorted(last.items(), key=lambda kv: kv[0])[-8:], "never:", last.get("never", 0))
# Monthly network view: share of active MIDAS/TMU sites with q>=90, by month, 2024-2026
print("\nActive MIDAS and TMU: share of site-days with quality >= 90, by month")
mon = collections.defaultdict(lambda: [0, 0])
for v in d.values():
    if v["status"] != "Active" or v["type"] == "TAME": continue
    for k, x in v["q"].items():
        if k >= "2023-01":
            m = mon[(v["type"], k[:7])]; m[1] += 1; m[0] += (x is not None and x >= 90)
for t in ("MIDAS", "TMU"):
    print(t, " ".join(f"{k[1][2:]}:{m[0]/m[1]:.0%}" for k, m in sorted(mon.items()) if k[0] == t))
