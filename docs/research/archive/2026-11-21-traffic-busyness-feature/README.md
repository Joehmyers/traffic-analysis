# Working papers: traffic busyness research, 21 November 2026

These two scripts produced the census of which WebTRIS sites actually report, in
section 2 of [`../../traffic-busyness-feature.md`](../../traffic-busyness-feature.md).

| File | What it does |
|---|---|
| `quality_census.py` | Draws a random sample (seed 42) of 100 active MIDAS, TMU and TAME sites and 100 inactive sites from a saved WebTRIS site list, then asks the API for each site's daily quality from 1 January 2018 to 30 June 2026. One request per site, one at a time. |
| `census_summary.py` | Turns the census file into the tables in the research document. |

To run them again:

```bash
curl -sS -o sites.json https://webtris.nationalhighways.co.uk/api/v1.0/sites
python3 -I quality_census.py sites.json quality_census.json
python3 -I census_summary.py quality_census.json
```

The census makes 400 requests and took about 10 minutes on 20 November 2026.
WebTRIS keeps only a rolling 10 years of data, and the site list may change, so a
later run will not match exactly.

The raw responses from that day (site list, census, sample reports, clock-change
days) are in `assets/webtris-research-2026-11-20/`, which the repository's Stop
hook uploads to the project's R2 bucket when R2 is configured.
