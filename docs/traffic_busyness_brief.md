# Traffic Busyness Feature — Brief

Nov 2026 · Joe Myers · Status: draft

---

## Goal

Build a traffic feature that tells our demand models one thing:
**are the roads in this area busier or quieter than normal, at this time?**

It should work at any **REGION × PERIOD** grain, in the same way as the
weather and extreme weather features in Hourly Forecasting. Change the
mapping tables, re-run, and get the feature at a new grain with the same code.

It replaces the old R tool in `Model/` (copied from the `Highways England` repo).
That tool makes one national monthly number (traffic ÷ licensed vehicles) and a
PDF of charts. It was never joined to a forecast.

---

## What we keep and what we change

| Old R tool | New feature |
| --- | --- |
| National, monthly | Any REGION × PERIOD (NCA, PGA, cluster; hour, 4-block, day) |
| Traffic ÷ licensed vehicles | Traffic ÷ the same sites' normal traffic (busyness ratio). Licensed vehicles kept only for the long-term trend |
| Fills gaps and treats zeros as missing | Keeps real zeros, flags gaps, never makes up values |
| Site list changes from run to run | Each ratio compares a site only with its own normal, so a changing site list does not bias it |
| CSV, RDS and PDF on a laptop | Delta tables in `research_forecasting`, run as a job |

---

## Core principle: natural grain, late aggregation

This is the same rule as Hourly Forecasting. Store each source at its own grain.
Aggregate only at the end, using the shared mapping tables in `dbreferences`.

| Axis | Atomic unit | Mapping | Example |
| --- | --- | --- | --- |
| Space | MAP_1K (OS 1 km square) | `partition_spatial_{region}` | `partition_spatial_nca` → 13 NCAs |
| Time | Hour (0–23) | `partition_temporal_{period}` | `partition_temporal_4block` → 4 blocks |

Each traffic site has a latitude and longitude, and its description also
carries the OS easting and northing ("GPS Ref"). So the OS 1 km square
(MAP_1K) comes straight from the site record. After that, the partition
tables do the rest. No new spatial logic is needed. (Checked: all 11,234
active sites have coordinates, and 11,233 land on a square in
`partition_spatial_nca`.)

**What a site is.** A site is a fixed counter on one carriageway, in one
direction, not a camera. Of the 11,234 active sites: 8,299 are MIDAS road
loops (motorways, about every 500 m), 2,635 are TMU counters and 300 are
TAME counters (mostly A-roads). Northbound and southbound are separate sites.

Traffic is a **count**, so sites are **summed** within a region. Weather is
an average, but traffic is not.

---

## Layer 1 — Source tables (natural grain)

Schema to be decided (e.g. `research_forecasting.traffic`).

| Table | Grain | Contents |
| --- | --- | --- |
| `traffic_sites` | site | Id, name, type (MIDAS, TMU, TAME), road, direction, lat, lon, easting, northing, MAP_1K, status, first and last seen. Road, direction and OS ref are parsed from the description. Keeps history, because sites open and close |
| `traffic_site_quality_daily` | site × date | Quality % from the API |
| `traffic_flow_15min` | site × 15 min (UTC) | Total volume as returned. Optional: length bands, average speed |
| `traffic_flow_hourly` | site × hour | Sum of 15-min volumes, plus how many of the 4 slots had data |
| `traffic_flow_normals_hourly` | site × day-of-week × hour × week-of-year | What "normal" looks like for that site. Built on a fixed reference window that leaves out COVID (Mar 2020 – Jul 2021) |
| `licensed_vehicles_quarterly` | local authority × quarter | Optional, from gov.uk VEH0105. For the long-term trend only |

History: from 2018-01-01 to match task data, or as far back as the API allows.

### Data on hand vs the daily source

**Sample already in this repo** (`API tests/`, `Documentation/`, `IP/`, saved by
the R tool's API test script in 2022). Enough to design the tables and test the
parsing before we have API access. Not enough to build normals or features.

| File | What it holds |
| --- | --- |
| `API tests/allSites.csv` | Full site list: 20,076 sites, 11,234 active, with lat/lon and OS ref. Used for every site check in this brief |
| `Documentation/allSites.csv` | Older site list: 19,355 sites, 11,169 active |
| `API tests/DailyReport.csv` | 15-min counts for one site (5013, M50/2092A), 1 Nov – 31 Dec 2022: 5,856 rows (61 days × 96 slots), 9 missing volumes. Has total volume, 4 length bands, 15 speed bands and average speed |
| `API tests/{November,December} 2022_{Days,DA,HA}.csv` | Monthly report for the same site: daily totals, average flow by weekday, hourly totals by weekday |
| `IP/veh0101.xlsx` (and `.ods`) | DfT licensed vehicles, Great Britain and UK totals by quarter. National only; regional work needs VEH0105 |

**Daily source in future.** The WebTRIS API, pulled each day into the Layer 1
tables once we have an outbound route (TB-102):
* `sites`, to keep the site list and its history up to date
* `quality/daily`, per site
* `reports/daily`, 15-min counts per site, loading only days not yet held

Plus the gov.uk VEH0105 table each quarter, if we keep licensed vehicles.

---

## Layer 2 — Aggregation (`region` + `period` parameters)

`build_traffic_features` → `traffic_{region}_{period}`, keyed on
(REGION, DATE_VALUE, PERIOD). This joins straight onto `demand_{region}_{period}`
and `features_{region}_{period}`.

| Column | Meaning |
| --- | --- |
| `TR_FLOW` | Sum of counts from sites that reported. Not a count of distinct vehicles (see below) |
| `TR_NORMAL` | What those same sites normally count at this time |
| `TR_RATIO` | `TR_FLOW / TR_NORMAL`. 1.10 = 10% busier than normal. **The main feature** |
| `TR_COVERAGE` | Share of the region's usual site-hours that actually reported |
| `TR_SITES` | Number of sites that reported |
| `TR_SPEED_RATIO` | Optional. Average speed against normal, as a congestion signal |

Rules:
* The ratio only uses sites that reported in that region-period, each against
  its own normal. Gaps lower `TR_COVERAGE`; they do not bias `TR_RATIO`.
* If coverage falls below a set level (start at 0.5), the ratio is NULL.
* No filling, no extrapolation.
* **The same car is counted many times.** A car driving 10 km of motorway
  passes about 20 MIDAS loops. So `TR_FLOW` is closer to vehicle-km than to
  vehicles, and dense motorway loops outweigh sparse A-road counters. The
  ratio to normal is not affected by this, because the same sites are on both
  sides. Demand weighting (see Possible expansions) would also deal with it.

**Worked example (made-up numbers).** NCA01 London, Mon 7 Dec 2026, 08:00–09:00.
1,050 of 1,200 usual sites report. Their counts add up to 950,000. Their normal for
a December Monday at 08:00 is 1,000,000. So `TR_RATIO` = 0.95 (5% quieter than
normal, perhaps because of snow) and `TR_COVERAGE` = 0.875.

A `partition_temporal_day` table (all 24 hours → one period) is needed for
daily models. Today only `hour` and `4block` exist.

---

## How models use it

Traffic is **observed, not forecast**. We do not know tomorrow's traffic today.
So it follows the same leakage rules as demand in the LASSO work:

* lags from the day the forecast is made: `TR_RATIO_DOFLAG1`, `TR_RATIO_ROLLMEAN7_LD`,
  `TR_RATIO_ROLLMEAN28_LD`
* never a same-day value for D ≥ 1

For medium and long-term models, the monthly ratio and the flow-per-licensed-vehicle
trend are the useful signals.

**Worth keeping if** adding the features to the LASSO candidate pool lowers
forecast error in the usual backtest (Sep 2025 – Aug 2026, all NCAs, D+0–7).

---

## Known limits (checked 20 Nov 2026)

**1. England only, and only motorways and major A-roads.** Active sites by
NCA (site list from the API test folder):

| NCA | Active sites |
| --- | --- |
| NCA01 | 1,287 |
| NCA02 | 683 |
| NCA03 | 693 |
| NCA04 | 2,004 |
| NCA05 | 609 |
| NCA06 (Wales) | 26 (border only) |
| NCA07 | 1,933 |
| NCA08 | 1,534 |
| NCA09 | 1,037 |
| NCA10 | 1,427 |
| NCA11, NCA12, NCA13 | 0 |

Wales, Northern Ireland and Scotland get NULL unless we find another source
(e.g. Traffic Scotland, Traffic Wales, or DfT counts). Much AA demand is on
local roads, which this data does not see.

**2. No internet from Databricks.** Serverless cannot reach any outside site
(the API, gov.uk and pypi.org all fail). We need an outbound route before any download.

**3. Data may arrive late.** The old user guide says to run only up to the end
of the month before last. If the data really lags 4–8 weeks, the feature cannot
help D+0–7 forecasts and only helps training and longer-term models. **Check this first: it decides whether a daily feature is worth building.**

**4. The API address is unconfirmed.** The R tool uses
`apptris-a-uks.azurewebsites.net`. We could not test it (see point 2).

---

## Licence and credit

**Status: to confirm (TB-101).** We could not check the source websites from
Databricks, so the points below are what we expect, not what we have checked.

| Source | Owner | Expected licence | Access |
| --- | --- | --- | --- |
| WebTRIS traffic counts (sites, quality, reports) | National Highways | Open Government Licence v3.0 | Public API, no key. The R scripts call it with no login |
| Licensed vehicles (VEH0101, VEH0105) | Department for Transport | Open Government Licence v3.0 | Free download from gov.uk |

What the Open Government Licence allows: copying, sharing and changing the data,
including for commercial use, as long as we credit the source.

What we must do:
* Credit both sources wherever the feature or its outputs are shown (reports,
  dashboards, decks, research notes).
* Keep a link to the licence and the date we checked it in the repo README.
* Follow any usage limits on the API (rate limits, fair use), especially for
  the backfill of about 11,000 sites over several years.
* Do not imply that National Highways or DfT endorse our work.

Credit lines (to use once the licence is confirmed):

> Contains National Highways information licensed under the Open Government Licence v3.0.

> Contains Department for Transport data licensed under the Open Government Licence v3.0.

Checks for TB-101:
1. Read the licence statement on the WebTRIS site and the API pages.
2. Read the licence on the gov.uk vehicle licensing statistics page.
3. Look for published API limits or terms of use.
4. Record what was found, the URLs and the date here, then remove "to confirm".

---

## Possible expansions

Not part of the first build. Worth testing once `TR_RATIO` works.

### Demand weighting

A counter on an empty stretch of the M6 tells us less than one beside a busy
town. So we could weight each site by the AA demand near it, using the 1 km squares.

How it would work:
1. Take each square's task count over a fixed window (e.g. Oct 2025 – Sep 2026).
2. Share that count among the sites within a set distance (start at 5 km), with
   nearer sites getting more. Each square's demand is shared out once, so a
   cluster of loops does not count it twice.
3. A site's weight is the total demand it was given.
4. `TR_RATIO_DW` = weighted sum of the sites' counts ÷ weighted sum of their normals.
   `TR_DEMAND_COVERAGE` = share of the region's demand near a site that reported.

New table: `traffic_site_demand_weights` (MAP_1K × site × version).

This is the same idea as `_w_point` in Hourly Forecasting (DTN points weighted
by area). Here the weights come from demand, not area. Squares with no demand
add no weight, which fits the full-grid rule: every square stays in the
partition table, but some have zero weight. It also stops dense motorway loops
outweighing A-road counters.

The weights are frozen per version. They must come from a window before any
backtest period, or demand leaks into the feature.

**How much demand is near a counter** (report tasks, Oct 2025 – Sep 2026,
3.75M tasks with a breakdown location; distance from each breakdown point to
the nearest active site):

| Distance | Share of tasks |
| --- | --- |
| within 1 km | 17% |
| within 2 km | 30% |
| within 5 km | 58% |
| within 10 km | 76% |

In the English NCAs, 55–80% of tasks are within 5 km of a site (NCA01 London
lowest at 55%, NCA09 highest at 80%). The median distance is 2.3–4.5 km. Wales
(NCA06) is 4% and Scotland 0%. NCA11 shows 40%, but that is likely wrong: its
breakdown points may be in the Irish grid, which this check did not convert.

Only 3.3% of tasks are flagged as on a motorway (`ARRIVED_ON_MWAY_IND`), and
76% of those are within 1 km of a site. So the traffic data sees the roads
near most demand, but very little demand happens on those roads. The feature
is a signal of how busy an area is, not a count of the cars that break down.

---

## Open questions

1. How far behind real time is the data? (decides scope)
2. Which outbound route: a cluster with internet, an allowlist, or files dropped into a volume?
3. What do we do for Wales, Northern Ireland and Scotland?
4. Do we need licensed vehicles at all, or is the ratio to normal enough?
5. Which reference window do we use for normals?

---

## Tasks

| ID | Task | Depends on |
| --- | --- | --- |
| **Access** | | |
| TB-101 | Confirm the API address, its limits, how late data arrives, and the licence for both sources | — |
| TB-102 | Agree an outbound route from Databricks | — |
| **Sources** | | |
| TB-201 | `traffic_sites` with MAP_1K and history (draft schema: Appendix A). Parsing can be built and tested on the sample now | TB-101, TB-102 |
| TB-202 | `traffic_site_quality_daily` | TB-201 |
| TB-203 | `traffic_flow_15min` backfill, then a daily load of only new data | TB-201 |
| TB-204 | `traffic_flow_hourly` | TB-203 |
| TB-205 | `traffic_flow_normals_hourly` | TB-204 |
| TB-206 | `licensed_vehicles_quarterly` (optional) | TB-102 |
| **Aggregation** | | |
| TB-301 | `partition_temporal_day` in `dbreferences` | — |
| TB-302 | `build_traffic_features` (region + period parameters) | TB-205, TB-301 |
| TB-303 | Checks: coverage by NCA, sums match across grains, no future dates | TB-302 |
| **Value** | | |
| TB-401 | Add TR features to the LASSO candidate pool and backtest | TB-302 |
| TB-402 | Keep or drop, written up | TB-401 |
| **Possible expansion** | | |
| TB-501 | Demand weighting: `traffic_site_demand_weights`, `TR_RATIO_DW`, `TR_DEMAND_COVERAGE`; choose distance and fall-off; backtest against `TR_RATIO` | TB-302 |

Critical path: TB-101 → TB-102 → TB-203 → TB-205 → TB-302 → TB-401.

---

## Appendix A — Draft schema: `traffic_sites`

Drafted from the sample site list (`API tests/allSites.csv`, 20,076 sites).
Two tables, following the append-only pattern we use for state tables:

* `traffic_sites_snapshots`: append-only. One row per site per load, exactly
  as the API returns it. Nothing is ever updated or deleted.
* `traffic_sites`: one row per site, rebuilt from the snapshots on each load.
  This is what everything else joins to.

### `traffic_sites_snapshots` (raw, append-only)

**Built 20 Nov 2026** as `research_forecasting.dev_joe_myers.traffic_sites_snapshots`
by `notebooks/01_load_sample_site_snapshots`: 39,431 rows (`sample_docs` 19,355,
`sample_2022` 20,076). The load inserts only new keys, so re-running it adds nothing.

| Column | Type | Notes |
| --- | --- | --- |
| `SNAPSHOT_DATE` | DATE | Day the list was pulled. Samples use estimates: `sample_docs` 2022-05-03 (creation date of the tech spec it sits with), `sample_2022` 2023-01-01 (pulled with `DailyReport.csv`, which already has the last slot of 31 Dec 2022) |
| `SNAPSHOT_DATE_IS_ESTIMATE` | BOOLEAN | TRUE for the two samples |
| `SOURCE` | STRING | `api`, or `sample_2022` / `sample_docs` for the two files on hand |
| `SITE_ID` | INT | API `Id` |
| `SITE_NAME` | STRING | API `Name` |
| `DESCRIPTION` | STRING | API `Description`, unchanged |
| `LATITUDE` | DOUBLE | API `Latitude` |
| `LONGITUDE` | DOUBLE | API `Longitude` |
| `STATUS` | STRING | API `Status` (`Active` / `Inactive`) |
| `SOURCE_FILE` | STRING | File path for sample loads; NULL for API pulls |
| `LOADED_AT` | TIMESTAMP | Load time |

Key: (`SNAPSHOT_DATE`, `SOURCE`, `SITE_ID`). The API's `Guid` field is empty
for every site, so we drop it.

The older file (`Documentation/allSites.csv`) has `Name` and `Description`
swapped. The load swaps them back and fails if any name still looks like a
description. Every site in the older list is also in the newer one (the newer
adds 721); 771 sites went from Active to Inactive between them and 117 the
other way.

### `traffic_sites` (one row per site)

| Column | Type | Rule | Sample check (active sites) |
| --- | --- | --- | --- |
| `SITE_ID` | INT, primary key | API `Id` | Unique |
| `SITE_NAME` | STRING | API `Name` | **Not unique**: 5 pairs of active A1M sites share a name. Always join on `SITE_ID` |
| `SITE_TYPE` | STRING | `MIDAS`, `TMU`, `TAME` or `LEGACY` ("Legacy Site … on road M6 …") from the start of the description. If there is no description, `MIDAS` when the name looks like `M1/3403A`, else `UNKNOWN` | 100% of active typed: 8,299 MIDAS, 2,635 TMU, 300 TAME. `LEGACY` is inactive only (2,023 sites) |
| `SITE_REF` | STRING | MIDAS: the name. TMU / TAME: the code after "Site" (e.g. `5608/1`, `30360196`) | 100% |
| `ROAD` | STRING | MIDAS: name before the `/` (e.g. `M1`). TMU / TAME: road after "on link" (e.g. `A34`, `A3(M)`) | 99.2%. 87 are link roads or roundabouts with no single road: NULL |
| `ROAD_CLASS` | STRING | `MOTORWAY` if `ROAD` starts with M or ends in `M` / `(M)`, `A_ROAD` if it starts with A, else NULL | 8,318 motorway, 2,829 A-road, 87 NULL |
| `DIRECTION` | STRING | Text after the last `;` in the description | 100%. 9 values: Northbound, Southbound, Eastbound, Westbound, Clockwise, Anti-clockwise, Carriageway Connector, Roundabout, Multi-Directional Slip |
| `LINK_ID` | STRING | MIDAS only: number after "on link" | 100% of MIDAS |
| `MIDAS_PRIORITY` | INT | MIDAS only: number after "priority" | 100% of MIDAS |
| `LATITUDE`, `LONGITUDE` | DOUBLE | From the API | 100% |
| `EASTING`, `NORTHING` | INT | OS grid metres from "GPS Ref: E;N". If missing, `st_transform` from lat/lon (EPSG 4326 → 27700) | 100%. Matches lat/lon to the metre on a 500-site check |
| `MAP_1K` | STRING | OS 1 km square from easting/northing, same format as `dbreferences` (e.g. `TQ3080`) | 11,233 of 11,234 land on a square in `partition_spatial_nca` |
| `STATUS` | STRING | Status in the latest snapshot | 11,234 Active, 8,842 Inactive |
| `DESCRIPTION` | STRING | Latest raw description | 2,941 sites (all inactive) have none |
| `FIRST_SEEN_DATE` | DATE | First snapshot with this site | — |
| `LAST_SEEN_DATE` | DATE | Latest snapshot with this site | — |
| `LAST_ACTIVE_DATE` | DATE | Latest snapshot where it was Active | — |
| `FIRST_DATA_DATE`, `LAST_DATA_DATE` | DATE | First and last day with counts in `traffic_flow_15min`. Filled once that table exists | — |
| `UPDATED_AT` | TIMESTAMP | Rebuild time | — |

Checks on every rebuild:
* `SITE_ID` is unique.
* Every active site has `SITE_TYPE`, `DIRECTION`, `EASTING`, `NORTHING` and `MAP_1K`.
* Easting/northing and lat/lon agree to within 50 m.
* The number of active sites has not changed by more than 5% since the last load (catches a broken pull).

Regions (NCA, PGA, cluster) are **not** stored here. They come from joining
`MAP_1K` to `partition_spatial_{region}`, so a new partition needs no change
to this table.
