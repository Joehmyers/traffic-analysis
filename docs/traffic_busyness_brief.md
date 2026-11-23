# Traffic Busyness Feature: Brief

Nov 2026 · Joe Myers · Status: draft · Updated 21 Nov 2026 with research findings
([research](research/traffic-busyness-feature.md), [build spec](specs/traffic-busyness-feature/spec.md))

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

## What the research found (21 Nov 2026)

Full findings, with sources: [`docs/research/traffic-busyness-feature.md`](research/traffic-busyness-feature.md).

1. **WebTRIS is monthly, so it cannot feed D+0–7 forecasts.** National Highways
   uploads data "a month in arrears" and has published nothing after 30 June
   2026. Its notice of 17 Sep 2026 expects the pause to last three to four
   months. This answers open question 1 and known limit 3.
2. **The only continuous feed of the same counters is NTIS**, National
   Highways' real-time subscriber service. Its sample messages carry flow,
   speed and occupancy for each site. It needs registration, a server we host
   to receive the data, and a mapping from its site IDs to WebTRIS IDs.
   Commercial feeds (TomTom, INRIX, HERE) give speed and congestion, not counts.
3. **So the first build changes shape.** Build the history from WebTRIS. Then,
   before chasing a continuous feed, test what one would be worth: backtest once
   as if data arrived the next day, and once with WebTRIS's real delay (see
   [How models use it](#how-models-use-it)).
4. **About a third of "Active" sites send no data.** In a sample of 300, 63% of
   MIDAS, 55% of TMU and 4% of TAME sites were usable in 2025. Coverage must
   count only sites that have a normal, not the status flag.
5. **Times are UK local clock time, not UTC.** One hour of counts is lost at
   every autumn clock change.
6. **No source builds normals by week of year.** Normals are now the median of
   the same weekday and hour within 14 days of the date, in the three previous
   years, rebuilt each year. This follows DfT and ONS practice and keeps the
   backtest free of leakage.
7. **The core design matches official practice.** DfT uses a matched set of
   counters and a ratio of sums. ONS's 2019 WebTRIS indicator averaged
   whichever sensors worked, and its own charts show false swings when sensors
   dropped out.
8. **Licence and access are confirmed (TB-101):** Open Government Licence, no
   key, no published limits. All four API addresses work.
9. **The allowlist route exists (TB-102):** an account admin can add the
   WebTRIS domain to the serverless network policy.

---

## What we keep and what we change

| Old R tool | New feature |
| --- | --- |
| National, monthly | Any REGION × PERIOD (NCA, PGA, cluster; hour, 4-block, day) |
| Traffic ÷ licensed vehicles | Traffic ÷ the same sites' normal traffic (busyness ratio). Licensed vehicles dropped: we run only hourly and daily models |
| Fills gaps and treats zeros as missing | Keeps real zeros, flags gaps, never makes up values |
| Site list changes from run to run | Each ratio compares a site only with its own normal, so a changing site list does not bias it |
| CSV, RDS and PDF on a laptop | Delta tables in `research_forecasting`, run as a job |
| Monthly WebTRIS data | History and monthly loads from WebTRIS. A daily feed (NTIS) only if the value test shows it is worth having |

---

## Core principle: natural grain, late aggregation

This is the same rule as Hourly Forecasting. Store each source at its own grain.
Aggregate only at the end, using the shared mapping tables in `dbreferences`.

| Axis | Atomic unit | Mapping | Example |
| --- | --- | --- | --- |
| Space | MAP_1K (OS 1 km square) | `partition_spatial_{region}` | `partition_spatial_nca` → 13 NCAs |
| Time | Hour (0–23) | `partition_temporal_{period}` | `partition_temporal_4block` → 4 blocks |

WebTRIS reports UK local clock time, not UTC. The hourly table keeps both, and
the features use whichever clock the demand tables use.

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
Not every active site reports: in a 2025 sample, 63% of MIDAS, 55% of TMU and
4% of TAME sites were usable (see the research census).

Traffic is a **count**, so sites are **summed** within a region. Weather is
an average, but traffic is not.

---

## Layer 1: source tables (natural grain)

Schema to be decided (e.g. `research_forecasting.traffic`).

| Table | Grain | Contents |
| --- | --- | --- |
| `traffic_sites` | site | Id, name, type (MIDAS, TMU, TAME), road, direction, lat, lon, easting, northing, MAP_1K, status, first and last seen. Road, direction and OS ref are parsed from the description. Keeps history, because sites open and close |
| `traffic_site_quality_daily` | site × date | Quality % from the API |
| `traffic_flow_15min` | site × 15 min, in UK local time as delivered, plus a UTC start time | Total volume as returned (an empty value becomes NULL and is never filled). Average speed and the four length bands. Speed bands are not stored: the API returns them empty |
| `traffic_flow_hourly` | site × hour | Sum of 15-min volumes, plus how many of the 4 slots had data. Local and UTC hour. Flags for clock-change hours and faulty site-days |
| `traffic_flow_normals_hourly` | site × date × hour × version | What "normal" looks like for that site: the median of the same weekday and hour within 14 days of that date, in each of the three previous years. Leaves out bank holidays, 24 Dec to 1 Jan, faulty days and COVID (Mar 2020 – Jul 2021). Bank holidays and Christmas days compare with the same day in earlier years. Rebuilt each year (version = year), so a normal never uses data from its own year or later |
| `traffic_load_log` | request | Which site-date ranges were requested, when, the HTTP status and the rows returned. Makes loads repeatable and shows what is missing |
| `licensed_vehicles_quarterly` | local authority × quarter | Not needed while we run only hourly and daily models. VEH0105 is quarterly; latest end June 2026 |

History: from 2018-01-01 to match task data. The API keeps only a rolling
10 years (on 20 Nov 2026 the earliest date it accepts is 20 Nov 2016), so we
must keep our own copy. To June 2026 that is about 3.35 billion 15-minute rows.

### Data on hand vs the daily source

**Sample already in this repo** (`API tests/`, `Documentation/`, `IP/`, saved by
the R tool's API test script in 2022). Enough to design the tables and test the
parsing before we have API access. Not enough to build normals or features.

| File | What it holds |
| --- | --- |
| `API tests/allSites.csv` | Full site list: 20,076 sites, 11,234 active, with lat/lon and OS ref. Used for every site check in this brief. The live API list on 20 Nov 2026 matches it in every count |
| `Documentation/allSites.csv` | Older site list: 19,355 sites, 11,169 active |
| `API tests/DailyReport.csv` | 15-min counts for one site (5013, M50/2092A), 1 Nov – 31 Dec 2022: 5,856 rows (61 days × 96 slots), 9 missing volumes. Has total volume, 4 length bands, 15 speed bands and average speed |
| `API tests/{November,December} 2022_{Days,DA,HA}.csv` | Monthly report for the same site: daily totals, average flow by weekday, hourly totals by weekday |
| `IP/veh0101.xlsx` (and `.ods`) | DfT licensed vehicles, Great Britain and UK totals by quarter. National only; regional work needs VEH0105 |

**The source in future.** The WebTRIS API, checked daily and loaded when new
data appears (in practice monthly, as WebTRIS uploads a month in arrears), into
the Layer 1 tables once we have an outbound route (TB-102):
* `sites`, to keep the site list and its history up to date
* `quality/daily`, per site
* `reports/daily`, 15-min counts per site (despite its name), loading only days
  not yet held, and fetching the latest two months again to catch late changes
  and the partial last day of each upload

Plus the gov.uk VEH0105 table each quarter, if we keep licensed vehicles.

If the value test justifies it, a continuous feed (NTIS) would write to the
same tables later.

---

## Layer 2: aggregation (`region` + `period` parameters)

`build_traffic_features` → `traffic_{region}_{period}`, keyed on
(REGION, DATE_VALUE, PERIOD). This joins straight onto `demand_{region}_{period}`
and `features_{region}_{period}`.

| Column | Meaning |
| --- | --- |
| `TR_FLOW` | Sum of counts from sites that reported. Not a count of distinct vehicles (see below) |
| `TR_NORMAL` | What those same sites normally count at this time |
| `TR_RATIO` | `TR_FLOW / TR_NORMAL`. 1.10 = 10% busier than normal. **The main feature** |
| `TR_COVERAGE` | Share of the expected normal traffic that reported. Expected means sites with a normal for that hour, not the Active flag. Weighted by normal volume, the same way `TR_RATIO` weights sites |
| `TR_SITES` | Number of sites that reported |
| `TR_SITES_EXPECTED` | Number of sites with a normal for that region-period |
| `TR_AVAILABLE_FROM` | The first date a forecast may use this value. For WebTRIS history: the 1st of the second month after the traffic date. For later loads: the day we first loaded it |
| `TR_SPEED_RATIO` | Optional. Average speed against normal, as a congestion signal |

Rules:
* The ratio only uses sites that reported in that region-period, each against
  its own normal. Gaps lower `TR_COVERAGE`; they do not bias `TR_RATIO`.
* Sites are matched hour by hour. A site-hour counts only if all 4 slots have a
  value (zeros count) and the site has a normal for that hour. The same
  site-hours go into both sums.
* If coverage falls below a set level (start at 0.5), the ratio is NULL. It is
  also NULL when fewer than a set number of sites are expected (start at 30).
  This is what makes Wales NULL: its 26 sites are all on the border.
* No filling, no extrapolation.
* Faulty site-days are left out, not deleted: a day whose total is below 5% or
  above 300% of its normal, or the same count of 10 or more in 8 or more slots
  in a row. These are starting values; no UK source publishes thresholds.
* Clock-change hours are left out of both sums.
* **The same car is counted many times.** A car driving 10 km of motorway
  passes about 20 MIDAS loops. So `TR_FLOW` is closer to vehicle-km than to
  vehicles, and dense motorway loops outweigh sparse A-road counters. The
  ratio to normal is not affected by this, because the same sites are on both
  sides. Demand weighting (see Possible expansions) would also deal with it.

**Worked example (made-up numbers).** NCA01 London, Mon 7 Dec 2026, 08:00–09:00.
1,050 of 1,200 expected sites report. Their counts add up to 950,000. Their normal
for a December Monday at 08:00 is 1,000,000, and the normal for all 1,200 expected
sites is 1,150,000. So `TR_RATIO` = 0.95 (5% quieter than normal, perhaps because
of snow) and `TR_COVERAGE` = 1,000,000 ÷ 1,150,000 = 0.87.

A `partition_temporal_day` table (all 24 hours → one period) is needed for
daily models. Today only `hour` and `4block` exist.

---

## How models use it

Traffic is **observed, not forecast**. We do not know tomorrow's traffic today.
So it follows the same leakage rules as demand in the LASSO work, plus one more:
a value can be used only once it has been published.

* Every row carries `TR_AVAILABLE_FROM`. A forecast made on day T uses only rows
  with `TR_AVAILABLE_FROM` on or before T.
* Lags are measured from the latest available day, not from T:
  `TR_RATIO_LAST`, `TR_RATIO_MEAN7`, `TR_RATIO_MEAN28`, and `TR_DATA_AGE_DAYS`
  (how old the latest value is). With a daily feed, `TR_RATIO_LAST` is the
  earlier `TR_RATIO_DOFLAG1`. With WebTRIS it is one to two months old.
* Never a same-day value for D ≥ 1.

We run hourly and daily models only, so the feature has to help those. There are no
monthly or longer-term models to make use of a value that arrives late.

**The value test (TB-401) runs twice**, with its rules written down before it runs:
1. *Daily feed:* as if each day's data arrived the next day. This measures what
   NTIS or another continuous feed would be worth.
2. *WebTRIS as published:* using `TR_AVAILABLE_FROM`. This measures what we can
   have today.

**Worth keeping if** adding the features to the LASSO candidate pool meaningfully
cuts forecast error for any day ahead from D+0 to D+7 in the usual backtest,
judged with a test built for nested models (Clark-West, pooled across NCAs).
What counts as meaningful is agreed before the run. If the daily version gives
no meaningful cut for any day, no traffic source will help D+0–7, and we stop.
If the daily version gains but the WebTRIS version does not, that makes the case
for NTIS.

WebTRIS has no data after 30 June 2026, so the backtest runs Sep 2025 – Jun 2026
(English NCAs, D+0–7) unless the missing months arrive first.

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

Treat these as upper bounds: about two thirds of active sites report in a given
year, and almost no TAME sites do.

Wales, Northern Ireland and Scotland get NULL. No open daily counts exist for
them: the Traffic Scotland and Traffic Wales feeds carry events and journey
times, and DfT counts are annual. The only continuous options are a commercial
speed feed, or Transport Scotland's own counters if it will share them. Much AA
demand is on local roads, which this data does not see.

**2. No internet from Databricks.** Serverless cannot reach any outside site
(the API, gov.uk and pypi.org all fail). We need an outbound route before any download.
Databricks documents a fix: an account admin adds the domain to the serverless
network policy (generally available on AWS and Google Cloud with the Enterprise
tier; on Azure it needs the Premium tier).

**3. Data arrives a month late, and is paused.** Confirmed: WebTRIS uploads
monthly, "a month in arrears", and has published nothing after 30 June 2026.
A daily feature cannot come from WebTRIS. It can still help training and the
value test.

**4. API address: confirmed.** `webtris.nationalhighways.co.uk/api/v1.0`, the
old Highways England address and the R tool's `apptris-a-uks.azurewebsites.net`
all return the same data. Use the National Highways address.

**5. Times are UK local time, not UTC.** Clocks-forward days have 23 hours of
slots. On clocks-back days the repeated hour appears twice, once blank, so one
hour of counts is lost.

**6. The site list has not changed since early 2023.** Its Active flag does
not say whether a site still reports, so coverage uses the data itself.

---

## Licence and credit

**Status: checked 20–21 Nov 2026 (TB-101)**, from outside Databricks.

| Source | Owner | Licence | Access |
| --- | --- | --- | --- |
| WebTRIS traffic counts (sites, quality, reports) | National Highways | Open Government Licence (WebTRIS terms page; it names no version). data.gov.uk also lists it as OGL | Public API, no key, no registration. No published rate limits |
| Licensed vehicles (VEH0101, VEH0105) | Department for Transport | Open Government Licence v3.0 (stated on the gov.uk release) | Free download from gov.uk, quarterly |

What the Open Government Licence allows: copying, sharing and changing the data,
including for commercial use, as long as we credit the source.

What we must do:
* Credit both sources wherever the feature or its outputs are shown (reports,
  dashboards, decks, research notes).
* Keep a link to the licence and the date we checked it in the repo README.
* Follow any usage limits on the API (rate limits, fair use), especially for
  the backfill of about 11,000 sites over several years. None are published, so
  keep the backfill to one or two requests at a time and tell National Highways
  (NetworkAnalysisandStatistics@nationalhighways.co.uk) before it starts.
* Do not imply that National Highways or DfT endorse our work.

Credit lines:

> Contains National Highways information licensed under the Open Government Licence v3.0.

> Contains Department for Transport data licensed under the Open Government Licence v3.0.

Checks for TB-101 (done):
1. WebTRIS licence: the terms section of https://webtris.nationalhighways.co.uk/Home/PrivacyPolicy
   says the information may be re-used "under the terms of the Open Government
   Licence" (© Crown copyright, 2023). The API page says it is "a free service and
   there is no need to register to use it".
2. Vehicle licensing: https://www.gov.uk/government/statistical-data-sets/vehicle-licensing-statistics-data-tables
   says "This publication is licensed under the terms of the Open Government
   Licence v3.0 except where otherwise stated".
3. API limits: no WebTRIS page publishes rate limits or terms of use beyond the
   licence and a standard disclaimer.
4. Recorded here on 21 Nov 2026.

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

### A continuous feed

Only if the daily version of the value test shows a gain. In order of fit:
1. **NTIS** (National Highways). Same counters as WebTRIS, about one minute
   behind real time, so our history and normals carry over. Needs a
   subscription, a server that receives pushed DATEX II messages, and a mapping
   from NTIS site IDs to WebTRIS IDs. Whether a private company can subscribe
   is not yet known (TB-103).
2. **A commercial speed feed** (TomTom, INRIX or HERE). Covers all UK roads,
   including Wales, Scotland and Northern Ireland, but measures speed and
   congestion, not counts. It would feed `TR_SPEED_RATIO`, not `TR_RATIO`. Paid.

---

## Open questions

1. ~~How far behind real time is the data?~~ **Answered:** WebTRIS uploads monthly,
   a month in arrears, and is paused after June 2026. A daily feature needs another
   source.
2. Which outbound route: a cluster with internet, an allowlist, or files dropped
   into a volume? **Recommended:** the serverless network policy (an account
   admin adds `webtris.nationalhighways.co.uk`). Files in a volume are the fallback.
3. What do we do for Wales, Northern Ireland and Scotland? **For now, NULL.** No
   open daily counts exist. Ask Transport Scotland (transtat@transport.gov.scot)
   whether it shares its 1,500 counters; otherwise a commercial speed feed.
4. ~~Do we need licensed vehicles at all, or is the ratio to normal enough?~~
   **Answered: no.** They only help long-term models, and we run hourly and daily
   models only. VEH0105 stays available (quarterly) if that changes.
5. ~~Which reference window do we use for normals?~~ **Answered:** the three
   previous years, rebuilt each year, leaving out COVID, bank holidays and faulty days.
6. Is a continuous feed worth getting? Decided by the daily version of the value test.
7. Can the AA subscribe to NTIS, on what terms, and how do its site IDs map to WebTRIS?
8. Which clock do the demand tables use, UK local time or UTC?
9. When will WebTRIS restart, and will it fill in July 2026 onwards? (Sign up on
   the WebTRIS News page, or ask NetworkAnalysisandStatistics@nationalhighways.co.uk.)

---

## Tasks

| ID | Task | Depends on |
| --- | --- | --- |
| **Access** | | |
| TB-101 | Confirm the API address, its limits, how late data arrives, and the licence for both sources. **Done 21 Nov 2026**, except the restart date | none |
| TB-102 | Agree an outbound route from Databricks. Ask an account admin to add `webtris.nationalhighways.co.uk` to the serverless network policy | none |
| TB-103 | Ask National Highways whether the AA can subscribe to NTIS: terms, cost, history, and how site IDs map to WebTRIS | none |
| **Sources** | | |
| TB-201 | `traffic_sites` with MAP_1K and history (draft schema: Appendix A). Parsing can be built and tested on the sample now | TB-101, TB-102 |
| TB-202 | `traffic_site_quality_daily` | TB-201 |
| TB-203 | `traffic_flow_15min` backfill, then a monthly load of only new data, with `traffic_load_log` | TB-202 |
| TB-204 | `traffic_flow_hourly` | TB-203 |
| TB-205 | `traffic_flow_normals_hourly`: three previous years, rebuilt each year | TB-204 |
| TB-206 | `licensed_vehicles_quarterly`: not needed while we run only hourly and daily models | TB-102 |
| **Aggregation** | | |
| TB-301 | `partition_temporal_day` in `dbreferences` | none |
| TB-302 | `build_traffic_features` (region + period parameters), with `TR_AVAILABLE_FROM` | TB-205, TB-301 |
| TB-303 | Checks: coverage by NCA, sums match across grains, no future dates, normals use only earlier years | TB-302 |
| **Value** | | |
| TB-401 | Add TR features to the LASSO candidate pool and backtest twice (daily feed; WebTRIS as published), rules written down first | TB-302 |
| TB-402 | Keep or drop, written up, and whether to pursue a continuous feed | TB-401 |
| **Possible expansion** | | |
| TB-501 | Demand weighting: `traffic_site_demand_weights`, `TR_RATIO_DW`, `TR_DEMAND_COVERAGE`; choose distance and fall-off; backtest against `TR_RATIO` | TB-302 |
| TB-502 | Continuous feed (NTIS first) writing to the Layer 1 tables | TB-402, TB-103 |

Critical path: TB-102 → TB-203 → TB-205 → TB-302 → TB-401.

---

## Appendix A: draft schema for `traffic_sites`

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
| `SITE_NAME` | STRING | API `Name` | **Not unique**: 5 pairs of active A1M sites share a name. Always join on `SITE_ID`. The API's report rows carry only the name, so never request two same-name sites in one call |
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
| `STATUS` | STRING | Status in the latest snapshot. Not a guide to whether the site reports (Known limits 6) | 11,234 Active, 8,842 Inactive |
| `DESCRIPTION` | STRING | Latest raw description | 2,941 sites (all inactive) have none |
| `FIRST_SEEN_DATE` | DATE | First snapshot with this site | n/a |
| `LAST_SEEN_DATE` | DATE | Latest snapshot with this site | n/a |
| `LAST_ACTIVE_DATE` | DATE | Latest snapshot where it was Active | n/a |
| `FIRST_DATA_DATE`, `LAST_DATA_DATE` | DATE | First and last day with counts in `traffic_flow_15min`. Filled once that table exists | n/a |
| `UPDATED_AT` | TIMESTAMP | Rebuild time | n/a |

Checks on every rebuild:
* `SITE_ID` is unique.
* Every active site has `SITE_TYPE`, `DIRECTION`, `EASTING`, `NORTHING` and `MAP_1K`.
* Easting/northing and lat/lon agree to within 50 m.
* The number of active sites has not changed by more than 5% since the last load (catches a broken pull).

Regions (NCA, PGA, cluster) are **not** stored here. They come from joining
`MAP_1K` to `partition_spatial_{region}`, so a new partition needs no change
to this table.
