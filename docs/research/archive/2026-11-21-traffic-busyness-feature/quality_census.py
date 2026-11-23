"""Stratified sample of WebTRIS sites: daily quality 2018-01-01..2026-06-30, one call per site."""
import json, sys, random, re, urllib.request, collections
sites_path, out_path = sys.argv[1], sys.argv[2]
B = "https://webtris.nationalhighways.co.uk/api/v1.0"
sites = json.load(open(sites_path))["sites"]
rng = random.Random(42)
strata = collections.defaultdict(list)
for s in sites:
    m = re.match(r"(MIDAS|TMU|TAME|Legacy)", s.get("Description") or "")
    t = m.group(1) if m else "none"
    strata[(t, s["Status"])].append(s)
plan = [(("MIDAS","Active"),100), (("TMU","Active"),100), (("TAME","Active"),100),
        (("MIDAS","Inactive"),40), (("TMU","Inactive"),30), (("TAME","Inactive"),30)]
out = {}
for key, n in plan:
    for s in rng.sample(strata[key], n):
        url = f"{B}/quality/daily?siteId={s['Id']}&start_date=01012018&end_date=30062026"
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                body = r.read()
            d = json.loads(body) if body else {"Qualities": []}
            err = None
        except Exception as e:
            d, err = {"Qualities": []}, str(e)
        out[s["Id"]] = {"type": key[0], "status": key[1], "name": s["Name"],
                        "lat": s["Latitude"], "lon": s["Longitude"],
                        "q": {x["Date"][:10]: x["Quality"] for x in d.get("Qualities", [])}, "error": err}
        print(key, s["Id"], len(out[s["Id"]]["q"]), err or "", flush=True)
json.dump(out, open(out_path, "w"))
print("DONE", len(out))
