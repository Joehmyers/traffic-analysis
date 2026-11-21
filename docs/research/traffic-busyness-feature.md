# Research: Can WebTRIS support a traffic busyness feature, and how should we build it?

> **Status:** Complete for the questions below, with the gaps listed under "Not established" and "Not checked". National Highways' NTIS subscriber documentation (on trafficengland.com) could not be read from this session.
> **Date:** 2026-11-21. API checks were run on 2026-11-20. WebTRIS is mid-change, so recheck anything about its schedule before relying on it.

## Question

The brief ([`docs/traffic_busyness_brief.md`](../traffic_busyness_brief.md)) wants a feature that tells AA demand models whether roads in an area are busier or quieter than normal, at any region × period grain, from National Highways' WebTRIS counts. This document asks:

1. Can WebTRIS feed a daily feature for forecasts made 0 to 7 days ahead (D+0 to D+7)? What do its address, licence, limits, publication delay, history and data format turn out to be?
2. Which sources could act as a continuous feed (daily or real time) instead?
3. How do official statistics bodies define a counter's "normal" traffic?
4. How do they combine many counters into one area figure when counters come and go or fail?
5. How should a feature that is observed (not forecast) and published late be used in short-horizon forecasts, and how should its value be tested?
6. What traffic data exists for Wales, Scotland and Northern Ireland, and is licensed-vehicle data still published?
7. How can outside data reach Databricks when serverless compute has no internet?

Constraints: England's strategic road network only (WebTRIS); Databricks serverless with no outbound internet today; the usual backtest runs Sep 2025 to Aug 2026, all NCAs (the 13 national areas), D+0 to D+7.

## Summary

WebTRIS cannot feed a D+0 to D+7 feature. National Highways loads it monthly, "a month in arrears", and has published nothing since 30 June 2026. It expects the pause to last three to four months from 17 September 2026. WebTRIS is still a good history source (data back to 20 November 2016, open licence, no key, 40,000-row pages), so it can support training data, monthly features and a test of what a faster feed would be worth. The only continuous feed of the same counters is NTIS, National Highways' real-time subscriber service: its published sample messages carry per-site loop data, but it needs registration, a server we host to receive pushed data, and site-ID mapping. Commercial feeds measure speed and congestion, not counts. Official practice supports the brief's core design (each counter against its own normal, a ratio of sums, no gap filling) but not two details: no source builds normals by week of year, and ONS advised rebasing every year rather than fixing one reference window. About a third of sites marked "Active" send no data, so coverage must be measured against sites that reported in the reference period.

## Definitions

- **WebTRIS**: National Highways' public website and API for historic counts from its road counters in England.
- **Site**: one counter on one carriageway, in one direction. There are three types. MIDAS (Motorway Incident Detection and Automatic Signalling) loops sit on motorways, about every 500 m. TMU (Traffic Monitoring Unit) and TAME (Traffic Appraisal, Modelling and Economics) counters sit mostly on A-roads. All are inductive loops: wire loops cut into the road surface that detect vehicles passing over them.
- **Slot**: one 15-minute interval in a WebTRIS report.
- **Quality %**: WebTRIS's completeness measure: minutes of data received ÷ (days × 1,440) × 100.
- **Normal**: what a site usually counts at a given hour on a given kind of day.
- **Busyness ratio** (`TR_RATIO`): the counts from sites that reported, summed, ÷ the same sites' normals, summed.
- **Coverage**: the share of a region's expected traffic that reported.
- **Publication delay**: the time between traffic passing a counter and the count reaching us.
- **Continuous feed**: a source that keeps delivering new data daily or in real time, so a forecast can use yesterday's traffic.
- **NTIS**: National Traffic Information Service, National Highways' real-time system. WebTRIS sites are NTIS measurement sites.
- **DATEX II**: the European standard format for exchanging traffic data between systems.
- **Ex-ante test**: a backtest that uses only data already published at each forecast date. An **ex-post test** uses data published later and overstates a feature's value.
- **Matched sample**: comparing only the units present in both periods being compared. DfT's term for its counters is ATC (automatic traffic counter).
- **OGL**: Open Government Licence.
- **Network policy** (Databricks): an account-level list of the outside hosts serverless compute may reach.
- **Unity Catalog volume**: a governed folder of files in Databricks.

## Findings

### 1. WebTRIS as a source (direct API checks, 20 November 2026)

**Address and access**
- All four addresses return the same JSON for site 5013: `webtris.nationalhighways.co.uk/api/v1.0` (and `/api/v1`), `webtris.highwaysengland.co.uk` and `apptris-a-uks.azurewebsites.net`, the address the R tool uses. The [API page](https://webtris.nationalhighways.co.uk/API/Swagger) still names the azurewebsites address as the API location and says: "This is a free service and there is no need to register to use it." The News page (6 February 2023) says the old Highways England address "will continue to function for the foreseeable future".
- No WebTRIS page (home, FAQ, News, API, terms, contact) states a rate limit or fair-use rule. The contact address is NetworkAnalysisandStatistics@nationalhighways.co.uk.

**Licence**
- WebTRIS terms ([privacy policy page](https://webtris.nationalhighways.co.uk/Home/PrivacyPolicy), "© Crown copyright, 2023"): "You may use and re-use the information featured on this web site (not including logos) free of charge in any format or medium, under the terms of the Open Government Licence." The page names no version.
- [data.gov.uk](https://www.data.gov.uk/dataset/9562c512-4a0b-45ee-b6ad-afc0f99b841f/highways-england-network-journey-time-and-traffic-flow-data) lists the dataset under "UK Open Government Licence (OGL)" (updated 28 December 2018). DfT's [Find Transport Data](https://findtransportdata.dft.gov.uk/dataset/webtris-traffic-flow-api) says "Free" and "Other (Open)" (updated 5 August 2022).

**Publication delay and the pause**
- [FAQ](https://webtris.nationalhighways.co.uk/Home/Faqs), item 8: "Data from the sensors on the network are usually processed and uploaded to the WebTRIS service a month in arrears."
- [News](https://webtris.nationalhighways.co.uk/Home/News), 17 September 2026: "we have reached a pause point in the monthly provision of new data while we work assessing the changes to the new data pipelines. All data within the rolling historic 10-year window will continue to be available in the interim. We expect the pause in service to last for around three to four months."
- National Highways has announced this interruption five times and moved it four times: April 2024 (notice of 8 March 2024), early 2025 (11 July 2024), mid-2025 (30 April 2025), mid-2026 (31 January 2026), then the pause itself (17 September 2026). Only the 2024 notice promises to "upload the data collected in the interim".
- The latest data is 30 June 2026, and only its first four slots (00:00 to 01:00 local time, which is 23:00 to 24:00 UTC on 29 June; quality 4%). The newest day of a load can therefore be partial.
- DfT's travel time statistics for the strategic road network also stop at June 2026, with "a pause in the availability of data for the SRN as National Highways undertake essential developments" ([DfT, 30 September 2026](https://www.gov.uk/government/statistics/travel-time-measures-for-the-strategic-road-network-england-and-local-a-roads-great-britain-july-2025-to-june-2026)).

**History and revisions**
- The API refuses start dates more than 10 years back. On 20 November 2026, 20 November 2016 worked and 19 November 2016 returned HTTP 400 with a misleading message about date format. One day of history drops off every day.
- Past data can change. A 2019 refresh of April 2015 data was to "populate WebTRIS with data that is currently missing ... as well as update any data that has been reprocessed" (News, 15 March 2019).

**Report format** (`reports/Daily`, which despite its name returns 15-minute rows)
- Fields: `Site Name`, `Report Date`, `Time Period Ending`, `Time Interval` (0 to 95), four length bands, 15 speed bands, `Avg mph`, `Total Volume`. Every value is a string. A missing value is an empty string, so a real zero (`"0"`) and a gap (`""`) can be told apart.
- Speed bands were empty in every row checked (six sites, June 2026). `Avg mph` was present on every row with data. The length bands did not add up to `Total Volume` on up to 84 of about 2,780 rows at each MIDAS site, so the total must be used as delivered.
- Real zeros occur. One A-road counter had 249 zero slots in June 2026, the longest run 9 slots (2¼ hours, at night).
- Rows carry the site name, not the site ID, and five pairs of active sites share a name (all on the A1(M)). A request for several sites can therefore return rows that cannot be told apart.
- Paging: `page_size` 40,000 works and 50,000 returns HTTP 400. One site-year (35,040 rows) is 16 MB of JSON and took 3.8 seconds.
- `quality/overall` returns one pooled figure for all the sites asked for, not one per site.

**Time**
- Times are UK local clock time, not UTC.
- On 29 March 2026 (clocks forward) a site has 92 slots; slots 4 to 7 are absent.
- On 26 October 2025 (clocks back) a site has 100 rows. The repeated hour appears twice, once with counts and once blank. So one hour of counts is missing every autumn, and the API does not say which of the two real hours the counts belong to.

**Site list**
- The live list holds 20,076 sites, 11,234 of them active (8,299 MIDAS, 2,635 TMU, 300 TAME). Every count the brief reports for its 2022 sample matches exactly, so the list appears not to have changed since early 2023.
- All active sites have coordinates and an OS grid reference. The nine direction values and five shared names match the brief.

**Backfill size (estimate)**
- 11,234 sites × 3,103 days (2018 to June 2026) × 96 slots is about 3.35 billion rows and 1.5 TB of JSON. That means about 90,000 requests of 40,000 rows, or about 100 hours one at a time.
- Calling `quality/daily` first (one call per site) and skipping site-periods with no data would cut this by a third or more.

### 2. Which "Active" sites actually report (census of 400 sites)

Method: a random sample (seed 42) of 100 active sites of each type, plus 100 inactive sites. Daily quality was pulled from 1 January 2018 to 30 June 2026. A site counts as "usable" in a year if at least 80% of its days have quality of 90% or more.

| Active sites | 2018 | 2019 | 2022 | 2023 | 2024 | 2025 | 2026 (Jan to Jun) |
|---|---|---|---|---|---|---|---|
| MIDAS: usable | 50% | 52% | 60% | 54% | 58% | 63% | 71% |
| TMU: usable | 52% | 62% | 39% | 34% | 36% | 55% | 65% |
| TAME: usable | 27% | 18% | 9% | 7% | 1% | 4% | 1% |
| MIDAS: no data all year | 30% | 29% | 21% | 17% | 19% | 22% | 17% |
| TAME: no data all year | 36% | 34% | 61% | 69% | 80% | 87% | 92% |

- Daily quality is nearly all or nothing. 59.5% of site-days score 0, 39.2% score 90 or more, and 1.4% fall between.
- 38 of the 300 sampled active sites sent no data at all from 2018 to June 2026.
- Inactive sites: 0 to 3% usable each year from 2021.
- With 100 sites per type, each share carries a 95% margin of about ±10 points.
- Inference: applying the 2025 and 2026 shares to the full list, between about 6,700 and 7,600 of the 11,234 active sites (60% to 68%) are usable in a recent year. Nearly all are MIDAS or TMU; almost no TAME site is.

### 3. Continuous feeds

Only NTIS delivers the same counters continuously. Every other continuous source either measures speed or congestion, covers a few towns, or is national only.

| Source | What it measures | Coverage | Delay | Access and terms | Fit for `TR_RATIO` |
|---|---|---|---|---|---|
| NTIS subscriber service ("MIDAS Loop Traffic Data", "TMU Loop Traffic Data") | Per-site flow rate, speed, headway, occupancy, per lane | England's strategic roads: the WebTRIS sites | Real time (about 1 minute; snippet) | Registered subscribers only; we host an endpoint that NTIS pushes to; site IDs need NTIS's reference model | Same counters as WebTRIS, so WebTRIS history and normals carry over |
| DfT daily domestic transport use by mode | Index of motor traffic vs the same weekday in early February 2020 | Great Britain as one figure, about 275 counters | 3 days to 5 weeks (monthly release) | Open, OGL | National only |
| ONS traffic camera activity | Vehicle counts from public camera images | 7 towns or areas, including Northern Ireland | Weekly releases | Open; paused since June 2024 | Towns only, now stopped |
| TomTom, INRIX, HERE | Speed, travel time, jam factor per road segment | All UK roads, including Scotland, Wales and Northern Ireland | About 1 minute | Commercial licence | Speed or congestion against normal, not counts |
| Traffic Scotland DATEX II | Traffic status, journey times, events | Scotland's trunk roads | Real time | Approved subscribers (authorities, information providers, road operators) | No counts |
| City sources (TfGM loops, Newcastle Urban Observatory) | Counts at signals or from cameras | Greater Manchester; 4 Newcastle sites | Real time to 5 minutes | Open (endpoints and terms unverified) | Too local |
| Mobile network data (O2 Motion, BT Active Intelligence) | People or devices travelling per path or area | UK | Not established | Commercial contract | Journeys, not counts at a point |

**NTIS** (primary sources: sample code from the NTIS contractor, Thales, published on GitHub)
- The [NTIS Subscriber Service information file](https://raw.githubusercontent.com/ntisservices/ntis-java-web-services-Release3.14/master/NTISSubscriberService-Information.txt) (repository created April 2021, last updated October 2023) lists the feed types. One is "MIDAS Loop Traffic Data": "raw (unprocessed) real-time traffic data measured by Motorway Incident Detection and Automatic Signalling (MIDAS) loop sensors in the road". "TMU Loop Traffic Data" is the same for TMU counters. Others are fused processed traffic data, ANPR journey times, events, and sign status.
- "The NTIS system pushes unsolicited data to Subscriber systems", and "the Subscriber system is required to implement the server-side endpoint of the web service". "The NTIS system publishes data to registered Subscribers only." Reading the data requires NTIS's reference model, downloaded from a subscriber portal.
- The [example MIDAS message](https://raw.githubusercontent.com/ntisservices/ntis-dotnet-web-services-Release3.2/master/exampleRequests/DeliverMIDASTrafficDataRequest.xml) holds, for each measurement site and lane, vehicle flow rate (vehicles per hour), average speed, headway and occupancy, with error flags such as "suspect equipment". Sites carry 32-character IDs, not WebTRIS numbers. The message is marked "restrictedToAuthoritiesTrafficOperatorsAndPublishers".
- DfT confirms that "Subscribers can access incidents, journey times, speeds, flows, variable message sign and matrix signal settings data via a DATEX II feed" ([DfT, 28 March 2023](https://www.gov.uk/guidance/local-authority-transport-data-sharing-case-studies)).
- National Highways has been moving NTIS onto cloud infrastructure since 2021, with Sopra Steria contracted "over the next five years" ([press release, 18 November 2021](https://www.gov.uk/government/news/groundbreaking-new-digital-system-to-help-keep-motorists-better-informed-and-traffic-moving)). Whether this work causes the WebTRIS pause is not stated anywhere.
- Search snippets only (pages not read): data reach subscribers "within one minute of collection from the roadside", and the service is free "if you agree to our Acceptable Use and Operational Policy" (trafficengland.com). A 2019 report from the Office of Rail and Road (ORR) says MIDAS flow and speed "should be updated every minute".

**Other official feeds**
- National Highways' [developer portal](https://developer.data.nationalhighways.co.uk) offers five APIs: closures, signs, speed limits, road features and speed-managed areas. None carries flows. It allows "10 calls per subscription key per minute".
- HALOGEN: "Registered HALOGEN users can download MIDAS traffic counting data", with a National Highways project sponsor ([gov.uk, 2012](https://www.gov.uk/guidance/how-to-use-the-highways-agencys-logging-environment-halogen)). Its current status is unknown.
- DfT's daily index is published "on the Thursday following the second Tuesday of each month" from "around 275 automatic traffic count sites across Great Britain", at Great Britain level only ([methodology note, updated 12 November 2026](https://www.gov.uk/government/statistics/daily-domestic-transport-use-by-mode/covid-19-transport-data-methodology-note)). From early 2027 it moves to a 2025 baseline, 7-day rolling averages and a year-on-year series ([planned changes, 15 October 2026](https://www.gov.uk/government/statistics/daily-domestic-transport-use-by-mode-user-engagement-2026/planned-changes-to-the-daily-domestic-transport-use-by-mode-publication)).
- [Traffic Scotland developer hub](https://www.traffic.gov.scot/traffic-scotland-developer-hub) (read 21 November 2026): DATEX II "for approved subscribers", offering traffic status and journey times, plus camera images by FTP for registered users. "Traffic Scotland must be clearly credited as the data source." There are no counts.
- [ONS camera method](https://www.ons.gov.uk/economy/economicoutputandproductivity/output/methodologies/usingtrafficcameraimagestoderiveanindicatorofbusynessexperimentalresearch): public cameras publish "new images ... usually every few minutes". ONS counted vehicles in them with machine learning for Durham, London, Manchester, the North East, Northern Ireland, Southend and Reading, and released hourly and daily series weekly. ONS says the data is "not suited for estimating the overall amount of vehicle or pedestrian movements".

**Commercial feeds** (vendor documentation seen only as search snippets, except INRIX's DfT catalogue entry)
- TomTom's Traffic Flow and Intermediate Traffic APIs report speeds and travel times per road segment, updated every minute. Its "Historical Traffic Volumes" product gives modelled volumes, not live counts.
- INRIX floating car data (positions reported by moving vehicles) is listed with "Update frequency: Live" and "Licence: Commercial Licence" ([DfT catalogue](https://findtransportdata.dft.gov.uk/dataset/floating-car-data--181aa03b31d)).
- HERE Traffic API v7 gives speed, free-flow speed and a jam factor from 0 (free flow) to 10 (stopped).
- Waze for Cities shares jams and alerts with government bodies and road operators, who agree not to publish the data. Whether a private company qualifies was not established.
- Mobile network data (O2 Motion, BT Active Intelligence) is sold under contract to bodies such as DfT and ONS. No published daily series of road volumes was found.

### 4. Defining a site's normal

- No source read builds normals at site × weekday × hour × week of year.
- DfT's daily index compares each day with the same weekday in one fixed week (early February 2020), and warns: "Over the course of the year, normal traffic can vary by +/– 20%" (methodology note above).
- Google's mobility baseline was the median of the same weekday over the fixed five weeks from 3 January to 6 February 2020 ([arXiv 2004.04145](https://arxiv.org/abs/2004.04145)). Critics called the assumption that those weeks were typical "very strong" ([arXiv 2009.10648](https://arxiv.org/abs/2009.10648)), and noted that "the baseline can be indeed corrupted by local festivities" ([arXiv 2007.07095](https://arxiv.org/abs/2007.07095)).
- Rebasing: ONS advised DfT on "updating the reference year used every year", and in tests on 1993 to 2013 rebased estimates came closer to final figures. A fixed base "may also become more of an issue the further the series moves away from it" ([DfT methodology review, quarterly methods, July 2018](https://assets.publishing.service.gov.uk/media/6333374dd3bf7f56768dc38e/traffic-statistics-methodology-review-quarterly-methods.pdf)).
- Neighbouring weeks: DfT fills a faulty hour with "the average flow ... recorded on the same weekday, site and hour for the preceding and following weeks, provided they do not fall on a bank holiday and the data is valid" ([DfT methodology review, overview, July 2018](https://assets.publishing.service.gov.uk/media/6333371cd3bf7f567eeb5cbe/traffic-statistics-methodology-review.pdf), para 2.18).
- Year on year: from 2027 DfT will compare with "364 days earlier", "adjusted for known, repeated days of abnormal usage (such as public holidays)" (planned changes above).
- COVID: DfT replaced 2020 with "the equivalent days' data from 2019", and found this more reliable than rolling averages, exponential smoothing or imputation ([DfT, 16 September 2020](https://www.gov.uk/government/publications/travel-time-measures-for-the-strategic-road-network-and-local-a-roads-july-2019-to-june-2020/impact-of-the-coronavirus-covid-19-pandemic-on-travel-time-measures)).
- Special days: DfT's "neutral days" exclude "all public holidays and school holidays" ([annual methodology note, 2022](https://assets.publishing.service.gov.uk/government/uploads/system/uploads/attachment_data/file/1107099/annual-methodology-note.pdf)). ONS's calendar adjustments cover bank holidays, Easter, Christmas, Boxing Day, royal events, COVID and storms, but not school holidays or clock changes ([ONS, 25 June 2025](https://www.ons.gov.uk/economy/economicoutputandproductivity/output/articles/economicactivityandsocialchangeintheukrealtimeindicatorsseasonaladjustment/2025-06-25)).
- Medians: DfT uses "a median of the individual ATC expansion factors" (annual methodology note), and Google a median of five days. A 2026 paper finds the median with the median absolute deviation "is more robust to outliers" than rules based on the mean ([arXiv 2603.04432](https://arxiv.org/abs/2603.04432)).
- Leakage: the denominator of a ratio must come from training data only. In scikit-learn's words, "if you ... divide by the average value, the average should be the average of the train subset" ([scikit-learn common pitfalls](https://github.com/scikit-learn/scikit-learn/blob/main/doc/common_pitfalls.rst)).

### 5. Combining counters into an area ratio

- Matched sets: DfT's road traffic estimates use "the same set of ATCs for the whole of the previous year" (DfT review overview), and a new counter is used only after "a contiguous period of 2 years of valid data" (DfT review, quarterly methods).
- Ratio of sums: DfT sums "the 'average daily flow of the quarter' to stratification level, and divide[s] this by the total for the previous year to produce a ratio" ([provisional estimates quality report, September 2021](https://assets.publishing.service.gov.uk/media/63332bf58fa8f51d20ab2f86/provisional-road-traffic-estimates-background-quality-report.pdf)). Strata are "area, road type, and traffic flow levels". ONS's review of that step: "No improvements were identified" (DfT review, quarterly methods).
- Unmatched averages fail. ONS's 2019 WebTRIS indicator took "the mean value per working sensor" and warned that a change may be "due to a change in the underlying sample rather than a real-world behavioural change" ([ONS Data Science Campus, 18 March 2019](https://datasciencecampus.ons.gov.uk/projects/faster-indicators-of-uk-economic-activity-road-traffic-data-for-england/)). ONS added: "if sensors drop out in an area of high traffic counts, the overall average will fall" ([ONS, 15 April 2019](https://www.ons.gov.uk/economy/economicoutputandproductivity/output/articles/economicactivityfasterindicatorsuk/januarytomarch2019)).
- Formula: `TR_RATIO` has the form price statisticians call Dutot (a ratio of averages). ONS prefers Dutot or Jevons (a geometric mean of ratios) to Carli (an average of ratios), which has "a known upward bias" ([ONS consumer price methods, revised 25 March 2026](https://www.ons.gov.uk/economy/inflationandpriceindices/methodologies/traditionaldataaggregatesinconsumerprices)). For counts, the bias of an average of ratios "does not decrease as the number of ratios used in the average increases", while a ratio of sums' bias shrinks as total counts grow ([arXiv 1106.0797](https://arxiv.org/abs/1106.0797)). Inference: this matters at night and on quiet A-roads, where hourly counts are small.
- Screening faulty data: no UK source publishes numeric thresholds. The California PeMS system judges each detector "either good or bad throughout the entire day" and flags stuck signals ([arXiv 0804.2982](https://arxiv.org/abs/0804.2982)). WebTRIS data has "data points with zero flows but a wide range of speed values" ([arXiv 1809.01887](https://arxiv.org/abs/1809.01887)). National Highways withholds data from sensors that fail validation "until the issue is resolved", and MIDAS stores "up to eight days" of data at the roadside while TMU and TAME store none (ONS Data Science Campus, above).
- Imputation: DfT fills faulty hours; ONS's WebTRIS indicator did not. No source sets a coverage level below which an index is withheld.

### 6. Using a late, observed feature in forecasts

- "Ex-ante forecasts are those that are made using only the information that is available in advance"; ex-post forecasts "are not genuine forecasts" ([Hyndman and Athanasopoulos, FPP3](https://otexts.com/fpp3/regression.html#sec-forecasting-regression)). Inference: with a publication delay of L days, the shortest honest lag for horizon h is h + L days. Under WebTRIS's schedule L is about 30 to 60 days, so the brief's `TR_RATIO_DOFLAG1` (yesterday's value) would be ex-post.
- Joining on publication date: Databricks defines leakage as using "feature values ... that were not available at the time the label was recorded", and its feature store joins "as of" a time ([Databricks, 11 September 2026](https://docs.databricks.com/aws/en/machine-learning/feature-store/time-series)).
- Hindsight inflates value. In one study, a model's top feature on revised data (mean SHAP score 0.778) scored 0.039 on data as first published ([arXiv 2608.09033](https://arxiv.org/abs/2608.09033), not peer reviewed).
- Tests: the candidate pool with traffic features contains the pool without them, so the plain Diebold-Mariano test is unreliable. The Clark-West adjustment is "the norm" for this case ([arXiv 2008.08387](https://arxiv.org/abs/2008.08387)). Panel tests ask whether equal accuracy holds "on average over all panel units and over time" ([arXiv 2003.02803](https://arxiv.org/abs/2003.02803)).
- LASSO picks one of several correlated predictors and discards the others (glmnet documentation). Standardising "imposes a much heavier penalty" on slow-moving predictors ([arXiv 1810.03140](https://arxiv.org/abs/1810.03140)).
- Declare the test before running it: how training and test sets are built, "what metrics will be used, and what baselines"; the test set is "used once and only once" ([arXiv 2311.18807](https://arxiv.org/abs/2311.18807)).
- No study was found that measures traffic as a predictor of breakdowns or roadside-assistance demand. In Swiss ambulance calls, traffic mattered "solely because [it is] correlated with the hour" ([arXiv 2501.03111](https://arxiv.org/abs/2501.03111)).

### 7. Wales, Scotland, Northern Ireland, and licensed vehicles

- No open daily or hourly counts were found for any of the three nations. DfT's finest open data is hourly counts from 7am to 7pm on one weekday per count point per year, for Great Britain only ([roadtraffic.dft.gov.uk](https://roadtraffic.dft.gov.uk/about)).
- Transport Scotland "operate over 1,500 automatic traffic counters" (DfT FAQ), but its open feeds carry events, journey times and traffic status. Traffic Wales feeds carry events, roadworks, cameras and signs ([traffic.wales/developers](https://traffic.wales/developers)). Northern Ireland had 60 automatic counter sites in 2024 and publishes annual summaries only.
- Licensed vehicles: VEH0105 is "Licensed vehicles at the end of the quarter by body type, fuel type, keepership (private and company) and upper and lower tier local authority: Great Britain and United Kingdom". It is quarterly; the latest period is end of June 2026 (published 23 September 2026) and the next release is due on 19 January 2027. The licence is OGL v3.0 ([gov.uk](https://www.gov.uk/government/statistical-data-sets/vehicle-licensing-statistics-data-tables)). VEH0101a and VEH1104 give totals by nation and region.
- DfT's quarterly traffic estimates (TRA25) and daily index cover Great Britain as one total. DfT publishes regional traffic annually only (TRA0103).

### 8. Getting the data into Databricks

- **Serverless network policy.** Account admins can allow named outside domains for serverless notebooks and jobs ([AWS docs, 18 November 2026](https://docs.databricks.com/aws/en/security/network/serverless-network-security/manage-network-policies)).
  - It is generally available on AWS (since 9 June 2025) and Google Cloud (since 9 February 2026), with the Enterprise tier.
  - On Azure the feature "requires the Premium tier" ([Microsoft Learn, 18 November 2026](https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/network-policies)).
  - A policy holds up to 100 domains, and denials are logged in `system.access.outbound_network`.
- **Classic job clusters** reach the internet through the customer's own network (a NAT gateway and firewall). Serverless-only workspaces have no classic compute.
- **Files from outside.** Volumes are "landing areas for raw data produced by external systems", and Auto Loader or COPY INTO load them without duplicates. Databricks names "A scheduled job lands the raw API responses as files in a Unity Catalog volume" as one pattern, and leaves paging, rate limits and retries to the user ([API ingestion, 5 November 2026](https://docs.databricks.com/aws/en/ldp/api-ingestion)).
- **Cloudflare R2** can be a Unity Catalog external location (Databricks Runtime 14.3 or later). But Databricks says R2 "is intended primarily for OpenSharing use cases", and Auto Loader does not list R2 as a source.
- **Table layout.** "With less than 1 TB of data, don't partition". Liquid clustering is recommended "for all new tables", with up to four keys and fewer for tables under 10 TB. Predictive optimisation maintains managed tables by default.
- **NTIS implication.** NTIS pushes data to the subscriber, so an NTIS feed needs an inbound web endpoint. That is a different job from opening an outbound route. (Inference.)

## Options compared

### Normals

| Option | Seasonality | Values per cell (3 reference years) | Leak-free in a backtest | Precedent |
|---|---|---|---|---|
| A. Brief: site × weekday × hour × week of year, one fixed window | Weekly | About 3 to 6 | Only if the window ends before the backtest | None found |
| B. Same weekday and hour, within 14 days of the date, in each of the 3 previous years; median; rebuilt each year | Weekly, smoothed | Up to 15 | Yes: uses earlier years only | DfT neighbouring-week fill, DfT 364-day comparison, ONS yearly rebasing, medians |
| C. The same day 364 days earlier | Weekly | 1 | Yes | DfT from 2027 (national) |
| D. One fixed base week | None | 1 to 5 | Yes | DfT 2020 to 2026, Google; criticised for ignoring seasons |

### Outbound route (TB-102)

| Route | Who acts | Status | Notes |
|---|---|---|---|
| Add WebTRIS to the serverless network policy | An account admin | Documented on AWS, Google Cloud and Azure | One change, no new system; the domain filter also admits other domains on the same IP address |
| Classic job cluster with internet | Platform admins: cluster policy and firewall rule | Standard | Impossible in a serverless-only workspace |
| A job outside Databricks lands files in a volume | Whoever runs that job | Documented pattern | A second system to run and secure |

## Contradictions

- Update frequency: Find Transport Data says "Live"; the WebTRIS FAQ says "a month in arrears" and the News page "monthly provision".
- Licence wording: WebTRIS terms and data.gov.uk say OGL; Find Transport Data says "Other (Open)". National Highways' developer portal (a separate service) cites OGL v2.0 with amendments in its terms and OGL v3.0 in its API metadata.
- DfT expansion factors: "median" in the 2022 methodology note, "average" in the 2024 quality report.
- Backfill after the pause: promised in March 2024, and not repeated in the four later notices.

## Not established

- Whether a private company can subscribe to NTIS. Also unknown: its terms, cost, history, and how NTIS site IDs map to WebTRIS IDs. Its documentation site, trafficengland.com, was blocked by this session's permission check.
- When WebTRIS restarts, and whether data from July 2026 onwards will be backfilled.
- When each past month was actually published. WebTRIS exposes no load dates, so historical publication delays can only be modelled by rule.
- Numeric screening thresholds for loop data from any UK source.
- Any study measuring traffic as a predictor of breakdowns or roadside-assistance demand.
- Open counts from Transport Scotland's 1,500 counters (contact: transtat@transport.gov.scot).
- Which cloud and tier the AA's Databricks workspace runs on.
- Commercial prices and licence terms for internal forecasting use (TomTom, INRIX, HERE).

## Not checked

- Whether busyness moves with AA demand at all. That is the build's value test (TB-401), not a search.
- The `partition_spatial_*` tables, which are not reachable from here. The brief's figure of 11,233 of 11,234 sites landing on an NCA square was not rechecked.
- Which clock the demand tables use (local time or UTC).
- How often WebTRIS revises counts after first publication. Only one notice (2019) mentions it.
- Reporting rates by NCA. The census is national.
- API behaviour under sustained load. There are no published limits, and the checks ran at about one request per second.
- Internal AA data that could act as a continuous feed, such as patrol fleet telematics.

## Recommendation

The author's judgement, not a finding:

1. Treat WebTRIS as a history source. Build the tables and normals from it, but do not promise a D+0 to D+7 feature from it.
2. Before chasing a continuous feed, measure what one would be worth. Run the backtest twice: once with a one-day publication delay (what NTIS would give) and once with WebTRIS's real delay. If the one-day version does not lower error, no traffic source will help these horizons.
3. If it does, NTIS is the first source to pursue, because it carries the same counters, so the history and normals carry over. Ask National Highways whether the AA can subscribe, on what terms, and how site IDs map. A commercial speed feed is the second choice, and the only continuous option for Wales, Scotland and Northern Ireland.
4. Change the normals to option B, rebuilt each year from the three previous years.
5. Measure coverage against sites that have a normal, not against the "Active" flag.
6. Ask an account admin to add `webtris.nationalhighways.co.uk` to the serverless network policy (TB-102).

What would change this: WebTRIS restarting with daily loads, or NTIS being open to the AA on simple terms, would make a D+0 to D+7 feature buildable now.

## Sources

Read in full unless marked "snippet only".

1. WebTRIS API, FAQ, News, terms and contact pages, https://webtris.nationalhighways.co.uk (read 20 November 2026): primary.
2. WebTRIS API responses: `sites`, `quality/daily`, `quality/overall`, `reports/Daily`, Swagger description (20 November 2026): primary, measured.
3. data.gov.uk WebTRIS entry (28 December 2018) and DfT Find Transport Data entry (5 August 2022): catalogue metadata.
4. NTIS Subscriber Service information file and example MIDAS message, github.com/ntisservices (2013 to 2023): primary, published by the NTIS contractor; older than the 2021 cloud move.
5. DfT, Local authority transport data sharing case studies (28 March 2023): official.
6. National Highways and Sopra Steria NTIS press release, gov.uk (18 November 2021): official.
7. trafficengland.com services and subscriber pages; ORR report on Highways England traffic data (March 2019): snippet only.
8. National Highways developer portal FAQ and terms (read 20 November 2026): primary.
9. HALOGEN guidance, gov.uk (2012): official, possibly out of date.
10. DfT daily domestic transport use: methodology note (12 November 2026), planned changes and engagement outcome (15 October 2026): official.
11. DfT travel time measures, July 2025 to June 2026 (30 September 2026): official.
12. DfT road traffic statistics methodology review: overview, quarterly methods, stratification (July 2018): official, reviewed by ONS.
13. DfT annual methodology note (2022) and road traffic estimates background quality report 2024 (12 June 2025): official.
14. DfT provisional road traffic estimates background quality report (September 2021): official.
15. DfT COVID impact on travel time measures (16 September 2020) and on flow weighting (30 September 2026): official.
16. DfT table TRA0305, traffic by month (May 2026): official.
17. ONS Data Science Campus, faster indicators from road traffic data (18 March 2019) and ONS faster indicators January to March 2019 (15 April 2019): official, experimental.
18. ONS, using traffic camera images to derive an indicator of busyness: official, experimental.
19. ONS, real-time indicators seasonal adjustment (25 June 2025) and consumer price aggregation methods (revised 25 March 2026): official.
20. Traffic Scotland developer hub (read 21 November 2026): official.
21. Traffic Wales developers page; DfI Northern Ireland traffic and travel information report 2024 (29 April 2026); OpenDataNI annual counts: official.
22. roadtraffic.dft.gov.uk about, FAQ and API pages (read 20 November 2026): official.
23. DfT vehicle licensing statistics tables, VEH0101 and VEH0105 (23 September 2026); TRA25 quarterly estimates (30 September 2026): official.
24. Databricks documentation: network policies, classic networking, volumes, R2 external locations, Auto Loader, COPY INTO, API ingestion, Python data sources, MERGE, jobs, partitions, liquid clustering, predictive optimisation, feature store time series (pages dated September to November 2026): vendor, primary.
25. Microsoft Learn, Azure Databricks serverless network policies (18 November 2026): vendor, primary.
26. Hyndman and Athanasopoulos, Forecasting: Principles and Practice, 3rd edition (online, updated 20 November 2026): textbook.
27. arXiv papers: 2004.04145 (Google mobility), 2009.10648, 2007.07095, 2603.04432, 1106.0797, 0804.2982 (PeMS), 1809.01887 (WebTRIS data), 2012.11361 (NTIS data use), 2008.08387, 2003.02803, 1810.03140, 2311.18807, 2501.03111, 2608.09033 (not peer reviewed). Peer review status varies; noted where it matters.
28. scikit-learn common pitfalls; glmnet documentation; R forecast package `dm.test`: library documentation.
29. DfT Find Transport Data, INRIX floating car data entry: catalogue metadata.
30. TomTom, HERE, Waze for Cities, O2 Motion, BT Active Intelligence, Newcastle Urban Observatory, TfGM SCOOT loops: snippet only.

---

_Next step: the brief is updated with these findings, and [`docs/specs/traffic-busyness-feature/spec.md`](../specs/traffic-busyness-feature/spec.md) specifies the build._
