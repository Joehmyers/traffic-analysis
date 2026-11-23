# Spec: Traffic busyness feature, first build

> **Status:** Draft (for review)

This spec turns the [brief](../../traffic_busyness_brief.md) into a build. The
evidence behind its choices is in
[`docs/research/traffic-busyness-feature.md`](../../research/traffic-busyness-feature.md).
Where this spec and the brief differ, this spec is newer.

## Definitions

- **Site**: one WebTRIS counter on one carriageway, in one direction (types MIDAS, TMU, TAME).
- **Slot**: one 15-minute interval. WebTRIS numbers them 0 to 95 in UK local clock time.
- **Site-hour**: one site in one clock hour. It is **complete** when all 4 of its slots have a value (a zero is a value; an empty slot is not).
- **Faulty site-day**: a site-day the rules in R11 mark as unreliable. It stays in the tables, flagged, and is skipped by normals and features.
- **Clock-change hour**: an hour that is missing or repeated because the clocks changed that day.
- **Normal**: the median count of a site in one hour, taken from comparable days in earlier years (R12 to R16).
- **Normals version**: the set of normals for one calendar year Y. It uses only data from the three years before Y.
- **Expected site-hour**: a site-hour that has a normal. **Matched site-hour**: an expected site-hour that is also complete, not faulty and not a clock-change hour.
- **Busyness ratio** (`TR_RATIO`): the sum of counts over matched site-hours ÷ the sum of their normals.
- **Coverage** (`TR_COVERAGE`): the normals of matched site-hours, summed, ÷ the normals of expected site-hours, summed.
- **Available-from date** (`TR_AVAILABLE_FROM`): the first date a forecast may use a value, given when the data is published.
- **Region, period**: the spatial and time groups defined by the mapping tables `partition_spatial_{region}` and `partition_temporal_{period}` in `dbreferences`.

## Problem

AA demand models do not know whether the roads in an area are busier or quieter
than normal. The old R tool made one national monthly number and was never joined to
a forecast. WebTRIS, the only open source of per-counter counts for England,
uploads monthly, "a month in arrears", and has published nothing since 30 June 2026.
So this build cannot deliver a feature for forecasts 0 to 7 days ahead from WebTRIS.
It delivers four things:

1. The history.
2. The feature at any region × period grain, with honest publication dates.
3. A test that answers two questions: does the WebTRIS feature, used as published, lower forecast error? And would a daily feed lower it enough to be worth getting?
4. Tables that a daily feed (NTIS) could write to later without changing the features.

## Scope

### In scope

- Loading WebTRIS sites, daily quality and 15-minute counts from 2018-01-01 into
  Delta tables in `research_forecasting`, then loading new data each month.
- Hourly site counts, faulty-day flags, and normals rebuilt each year.
- `build_traffic_features(region, period)` writing `traffic_{region}_{period}`.
- `partition_temporal_day` in `dbreferences`.
- Lag features for the LASSO candidate pool, built only from published values.
- The value test (TB-401) and its write-up (TB-402), with rules fixed before the run.
- Data checks after every load and build (TB-303).
- Code, tests and verification set-up in this repository.
- Credit lines and licence links in the README.

### Out of scope

- A production feature for D+0 to D+7. It needs a daily source, and the value test decides whether one is worth getting.
- NTIS or any commercial feed (TB-502, a separate spec).
- Wales, Scotland and Northern Ireland, which stay NULL.
- Licensed vehicles (VEH0105). This is optional, and suggested for later.
- Demand weighting (TB-501).
- `TR_SPEED_RATIO`. Average speed is stored so it can be added later.
- Any filling of missing data.
- Dashboards or reports beyond the coverage report in R26.

Feature tables live in Databricks, next to the demand tables they join. This leaves
[D-0001](../../decisions/D-0001-use-cloudflare-r2-for-project-storage.md)
unaffected: R2 still holds project files kept outside git, such as data samples too
large to be test fixtures.

## Requirements

### Code and verification

1. The code lives in this repository as a Python package, `src/traffic_busyness/`, with
   Databricks job entry points that call it. The rules that decide a number have unit tests
   that run in `ops/check.sh` without Databricks: parsing, clock conversion, fault rules,
   choosing reference days, medians, ratio and coverage maths, and availability dates.
2. `ops/check.sh` runs a linter (ruff) and the tests (pytest), and `ops/setup.sh` installs
   the dependencies. CI runs `ops/check.sh` and passes.
3. The API address, catalog and schema names, date range and every threshold in this spec
   are named settings with the defaults given here, not values written inline.

### Loading WebTRIS (Layer 1)

4. **Sites.** `traffic_sites_snapshots` and `traffic_sites` follow the brief's Appendix A:
   append-only snapshots, a rebuilt one-row-per-site table, its parsing rules and its four
   checks.
5. **Daily quality.** `traffic_site_quality_daily` holds one row per site and date from
   2018-01-01 (`SITE_ID`, `DATE`, `QUALITY_PCT`, `LOADED_AT`), for every site in the list,
   active or not.
6. **15-minute counts.** `traffic_flow_15min` holds one row per site and slot, keyed on
   (`SITE_ID`, `SLOT_START_UTC`). The columns are listed under Interfaces.
   - a. An empty string loads as NULL and `"0"` loads as 0. Nothing is filled.
   - b. Local times convert to UTC using Europe/London rules. On a clocks-back day, the
     repeated hour's row that carries values is kept and assigned to the first (BST) hour,
     and its blank twin is dropped. Both real hours are flagged `IS_CLOCK_CHANGE_HOUR`.
   - c. Every row belongs to exactly one `SITE_ID`. A request never contains two sites that
     share a name, because report rows carry only the name.
   - d. The backfill asks only for site-months where `traffic_site_quality_daily` shows
     data on at least one day.
   - e. `TOTAL_VOLUME` is stored as delivered, even where the length bands do not add up to it.
7. **Repeatable loads.** Running the same load twice leaves every table with the same rows and
   values. Each monthly run fetches the latest two months again, which picks up late changes
   and the partial last day of each upload. A changed value overwrites the old one, and the
   load log records how many values changed.
8. **Load log.** `traffic_load_log` holds one row per API request: `LOAD_ID`, `SITE_IDS`,
   `START_DATE`, `END_DATE`, `PAGE`, `HTTP_STATUS`, `ROWS_RETURNED`, `ROWS_CHANGED`,
   `REQUESTED_AT`, `DURATION_MS` and `ERROR`.
9. **Gentle and fail-safe.**
   - At most 2 requests are in flight at once, with up to 40,000 rows per page.
   - Timeouts and 5xx responses are retried 3 times with growing waits.
   - A run fails if more than 5% of its requests fail.
   - HTTP 204 means "no data" and is not an error.
   - A start date older than the API's 10-year window is reported as such, not as a format error.
10. **Hourly counts.** `traffic_flow_hourly` holds one row per site and hour, keyed on
    (`SITE_ID`, `HOUR_START_UTC`). It holds:
    - `DATE_LOCAL` and `HOUR_LOCAL`;
    - `VOLUME`, the sum of the non-NULL slots;
    - `SLOTS_PRESENT` (0 to 4) and `IS_COMPLETE` (`SLOTS_PRESENT` = 4);
    - `AVG_MPH`, weighted by slot volume;
    - `IS_CLOCK_CHANGE_HOUR` and `IS_FAULTY_DAY`.

### Faulty site-days

11. A site-day is faulty when any of these holds. Each threshold is a setting, and these are
    its starting value:
    - **Stuck:** the same `TOTAL_VOLUME` of 10 or more in 8 or more slots in a row.
    - **All zero:** every present slot is 0, and at least 48 slots are present.
    - **Off scale** (applied by features only, because it needs a normal): the day's volume
      over its complete hours is below 5% or above 300% of the same hours' normals.

    No UK source publishes thresholds, so the plan reviews how many site-days each rule flags
    in 2025 before the full backfill is used.

### Normals

12. `traffic_flow_normals_hourly` holds one row per version, site, local date and local hour.
    Its key is (`NORMALS_VERSION`, `SITE_ID`, `DATE_LOCAL`, `HOUR_LOCAL`), and its columns are
    `NORMAL_VOLUME` (the median), `N_OBS`, `DAY_TYPE` (`ORDINARY`, `BANK_HOLIDAY`, `CHRISTMAS`)
    and `MAX_REF_DATE`.
13. Version Y covers every date in year Y. It uses only site-hours from years Y−3 to Y−1 that
    are complete, not faulty, and not clock-change hours. So `MAX_REF_DATE` is always before
    1 January of year Y.
14. The reference days depend on the kind of date. For an ordinary date, they are:
    - the same weekday,
    - within 14 days of the date's calendar day (crossing year ends),
    - in years Y−3 to Y−1,
    - ordinary days only,
    - outside 1 March 2020 to 31 July 2021.

    A bank holiday in England and Wales uses the same named holiday in years Y−3 to Y−1.
    24 December to 1 January use the same calendar date in years Y−3 to Y−1. A one-off bank
    holiday has no reference days, and so no normal.
15. A normal is NULL when `N_OBS` is below 6 for an ordinary date, or below 2 for a bank
    holiday or Christmas date.
16. Bank holidays come from a calendar in `dbreferences` if one exists. Otherwise a new
    `calendar_bank_holidays` table is built from gov.uk's bank holiday list, from 2015 onwards.

### Features (Layer 2)

17. `build_traffic_features(region, period)` writes `traffic_{region}_{period}`, keyed on
    (`REGION`, `DATE_VALUE`, `PERIOD`). It reads only `traffic_sites`, `traffic_flow_hourly`,
    `traffic_flow_normals_hourly`, `partition_spatial_{region}` and
    `partition_temporal_{period}`. It contains no code specific to a region or period.
18. For each region-period:
    - `TR_FLOW` = the sum of volume over matched site-hours.
    - `TR_NORMAL` = the sum of their normals.
    - `TR_COVERAGE` = `TR_NORMAL` ÷ the sum of normals over expected site-hours.
    - `TR_SITES` and `TR_SITES_EXPECTED` = the distinct sites among matched and expected site-hours.
    - `TR_RATIO` = `TR_FLOW` ÷ `TR_NORMAL`. It is NULL when `TR_COVERAGE` < 0.5, when
      `TR_SITES_EXPECTED` < 30, or when `TR_NORMAL` is 0.
    - `TR_NORMALS_VERSION` records the normals version used.
19. Every combination of region, date and period in the partitions and date range has a row,
    including regions with no sites. Those rows have `TR_SITES_EXPECTED` = 0 and a NULL ratio.
20. `TR_AVAILABLE_FROM` depends on how the data arrived:
    - For data loaded by the backfill, it is the 1st of the second month after the traffic
      date's month. For example, 15 January 2026 becomes 1 March 2026.
    - For data first loaded by a later monthly run, it is the later of that rule date and the
      first load date of the rows behind it.
21. The clock used to join `partition_temporal_{period}` (UK local or UTC) is a setting. It
    must match `demand_{region}_{period}` (open question 1).
22. `dbreferences.partition_temporal_day` maps hours 0 to 23 to a single period.

### Model inputs and the value test

23. A function builds lag features for each region, given a forecast date T and an
    availability rule. The rule `webtris` uses `TR_AVAILABLE_FROM`; the rule `daily` treats
    each day's value as available the next day. The features are:
    - `TR_RATIO_LAST`: the ratio on the latest available day.
    - `TR_RATIO_MEAN7` and `TR_RATIO_MEAN28`: the mean ratio over the latest 7 and 28
      available days, ignoring NULLs. They are NULL if fewer than half of those days have a value.
    - `TR_DATA_AGE_DAYS`: T minus the latest available day.

    The features come from the day-period table, and no value with an availability date after
    T is ever used.
24. The value test runs as planned in `docs/plans/traffic-busyness-feature.md`, and its rules
    are agreed and committed before the first run:
    - **Comparison:** the current LASSO candidate pool against the same pool plus the R23
      features, under both availability rules.
    - **Window:** forecast dates 1 September 2025 to 30 June 2026, extended to August 2026 if
      WebTRIS fills July and August first.
    - **Coverage:** English NCAs (NCA01 to NCA05, NCA07 to NCA10), horizons D+0 to D+7, with
      the backtest's usual error measure.
    - **Test:** a Clark-West test for each horizon on errors pooled across NCAs, one-sided, at
      the 5% level.
    - **Rule for pursuing a daily feed:** the `daily` version meaningfully lowers pooled
      error for any horizon from D+0 to D+7, and the test rejects there.
    - **Rule for keeping the WebTRIS feature:** the `webtris` version also meaningfully
      lowers pooled error.
    - **Meaningful:** the size of cut that counts is agreed and recorded in the plan before
      the first run.
25. As a diagnostic, not a forecast test, the plan reports the correlation by NCA between the
    baseline model's daily errors and the same day's `TR_RATIO`. If busyness and demand
    errors are unrelated even on the same day, that is worth knowing before reading the
    value test.

### Checks (TB-303)

26. A checks job runs after every load and build. It fails the run on any breach of the
    first six checks:
    - a. Every table's key is unique.
    - b. No slot or feature row is dated after the load's end date or after today.
    - c. 0 ≤ `TR_COVERAGE` ≤ 1, `TR_SITES` ≤ `TR_SITES_EXPECTED`, and `TR_RATIO` is NULL
      wherever R18 says it must be.
    - d. For every region and date, `TR_FLOW` and `TR_NORMAL` in the day-period table equal
      the sums of the hour-period values (normals within 0.001).
    - e. No normals row in version Y has `MAX_REF_DATE` on or after 1 January of year Y.
    - f. The site-list checks from the brief's Appendix A pass.
    - g. Every active site's `MAP_1K` maps to exactly one region in each partition used. The
      job lists unmapped sites but does not fail on them.
    - h. A monthly coverage report by NCA is written. The job warns when an English NCA's mean
      coverage falls by more than 0.2 from the month before.

### Licence and credit

27. The README gains a "Data sources and licences" section. It holds both credit lines from
    the brief, links to the licences, and the date checked (21 November 2026). Any output that
    shows the feature carries the National Highways credit line.

## Interfaces / files involved

### Tables (the data contract)

| Table | Key | Columns |
|---|---|---|
| `traffic_sites_snapshots`, `traffic_sites` | brief Appendix A | brief Appendix A |
| `traffic_site_quality_daily` | `SITE_ID`, `DATE` | `QUALITY_PCT`, `LOADED_AT` |
| `traffic_flow_15min` | `SITE_ID`, `SLOT_START_UTC` | `REPORT_DATE_LOCAL`, `TIME_INTERVAL`, `SLOT_START_LOCAL`, `TOTAL_VOLUME`, `AVG_MPH`, `LENGTH_0_520_CM`, `LENGTH_521_660_CM`, `LENGTH_661_1160_CM`, `LENGTH_1160_PLUS_CM`, `IS_CLOCK_CHANGE_HOUR`, `SOURCE`, `LOAD_ID`, `LOADED_AT` |
| `traffic_flow_hourly` | `SITE_ID`, `HOUR_START_UTC` | `DATE_LOCAL`, `HOUR_LOCAL`, `VOLUME`, `SLOTS_PRESENT`, `IS_COMPLETE`, `AVG_MPH`, `IS_CLOCK_CHANGE_HOUR`, `IS_FAULTY_DAY` |
| `traffic_flow_normals_hourly` | `NORMALS_VERSION`, `SITE_ID`, `DATE_LOCAL`, `HOUR_LOCAL` | `NORMAL_VOLUME`, `N_OBS`, `DAY_TYPE`, `MAX_REF_DATE` |
| `traffic_load_log` | `LOAD_ID`, `REQUESTED_AT`, `SITE_IDS`, `PAGE` | R8 |
| `traffic_{region}_{period}` | `REGION`, `DATE_VALUE`, `PERIOD` | `TR_FLOW`, `TR_NORMAL`, `TR_RATIO`, `TR_COVERAGE`, `TR_SITES`, `TR_SITES_EXPECTED`, `TR_AVAILABLE_FROM`, `TR_NORMALS_VERSION` |
| `dbreferences.partition_temporal_day` | `HOUR` | `PERIOD` |
| `calendar_bank_holidays` (only if `dbreferences` has none) | `DATE` | `NAME` |

All tables are Unity Catalog managed tables, liquid clustered and not partitioned, as
Databricks advises for tables under 1 TB. The 15-minute and hourly tables cluster on date,
then site.

### Files

| File | Change |
|---|---|
| `pyproject.toml` | New: package metadata, dependencies, ruff and pytest settings |
| `src/traffic_busyness/settings.py` | New: named settings and thresholds (R3) |
| `src/traffic_busyness/webtris.py` | New: API client with paging, retries, request limits, 204 and 400 handling (R6 to R9) |
| `src/traffic_busyness/parse.py` | New: site description rules (Appendix A) and report rows to typed records (R6) |
| `src/traffic_busyness/clock.py` | New: local slot to UTC, clock-change rules (R6b) |
| `src/traffic_busyness/faults.py` | New: faulty site-day rules (R11) |
| `src/traffic_busyness/normals.py` | New: reference days, medians, versions (R12 to R16) |
| `src/traffic_busyness/features.py` | New: matching, ratio, coverage, NULL rules, availability dates, lag features (R17 to R23) |
| `src/traffic_busyness/jobs/` | New: Databricks entry points: load sites, load quality, load flows, build hourly, build normals, build features, run checks |
| `tests/test_*.py` | New: one test module per module above |
| `tests/fixtures/webtris/` | New: trimmed real API responses (one site-day, both clock-change days, a day with missing slots, a site list extract with the shared-name pairs), each under 100 KB |
| `ops/check.sh`, `ops/setup.sh` | Fill in the lint, test and install commands |
| `.github/workflows/ci.yml` | Add the Python set-up step |
| Job definitions (the team's usual tool, for example a Databricks asset bundle) | New: the monthly load and build, and a one-off backfill |
| `README.md` | Data sources, licences and credit lines (R27) |
| `docs/diagrams/system-diagram.md` | Replace the placeholder with the real flow |
| `docs/plans/traffic-busyness-feature.md` | New: the plan, including the value-test rules (R24) |

## Acceptance criteria

- [ ] `ops/check.sh` runs the linter and the tests, and passes locally and in CI.
- [ ] Fixture tests show that `""` loads as NULL and `"0"` as 0; that the clocks-forward day has 92 slots and nothing at 01:00 to 01:59 local; that the clocks-back day yields 96 distinct UTC slots with the repeated hour flagged; and that two sites sharing a name never share a request.
- [ ] In a development schema, a load of 50 sites for January to June 2026 matches the WebTRIS website's downloaded report for 3 chosen site-days, value for value.
- [ ] Running that load a second time changes no row (same row count and checksum).
- [ ] After the backfill, every site-day with quality above 0 in `traffic_site_quality_daily` (2018-01-01 to the latest WebTRIS date) has rows in `traffic_flow_15min`, and the load log explains every gap.
- [ ] Normals version 2026 has no `MAX_REF_DATE` in 2026 or later, and changing a 2026 count in a fixture leaves the version 2026 normals unchanged.
- [ ] `build_traffic_features` runs for NCA × hour, NCA × 4-block, NCA × day and one other region partition, with no code change.
- [ ] For every NCA and date, the day-period `TR_FLOW` and `TR_NORMAL` equal the sums of the hour-period values.
- [ ] NCA06, NCA11, NCA12 and NCA13 have rows for every date, all with `TR_RATIO` NULL.
- [ ] The coverage report by NCA and month exists for 2018 to the latest WebTRIS month.
- [ ] No feature value used in the value test has an availability date after its forecast date, and the test run logs the count of such values (0).
- [ ] The value-test rules were committed before the first run, and the TB-402 write-up reports both versions against them.
- [ ] The README carries both credit lines, the licence links and the date checked.

## End-to-end verification

1. Run `ops/check.sh`: lint and unit tests pass.
2. In a development schema (for example `research_forecasting.dev_<user>`), load the site
   list, then quality and 15-minute counts for 50 sites for January to June 2026. Download
   the same 3 site-days from the WebTRIS website and compare them value for value.
3. Run step 2 again. Compare row counts and table checksums before and after.
4. Build hourly counts, normals versions 2025 and 2026, and features for NCA × hour,
   4-block and day. Run the checks job; every check passes.
5. Plot the national day ratio (all English NCAs) for 2025. Name a cause for each of the
   5 lowest and 5 highest days (snow, storm, major closure, a holiday mismatch). Investigate
   any day without one before going further.
6. Run the backfill in the production schema, run the checks again, and read the coverage
   report.
7. Commit the value-test rules to the plan, run the test under both availability rules, and
   write up TB-402.

## Open questions (to settle before planning)

1. Which clock do `demand_{region}_{period}` and `partition_temporal_hour` use, UK local or UTC? (R21)
2. Does `dbreferences` already hold a bank holiday calendar? (R16)
3. Which schema: `research_forecasting.traffic`, as the brief suggests?
4. The outbound route (TB-102) blocks the backfill. Parsing, rules and tests can be built
   now against the fixtures.
5. What size of error cut counts as meaningful? (R24)
6. Should the code live in this repository (assumed here) or beside Hourly Forecasting?
7. Which region partitions besides NCA are needed first (PGA, cluster)?

---

_Related plan: [docs/plans/traffic-busyness-feature.md](../../plans/traffic-busyness-feature.md) (to be written after this spec is approved)_
