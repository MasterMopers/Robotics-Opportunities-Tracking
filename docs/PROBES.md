# Endpoint probes (Phase 0b) and calibration notes (Phase 0c)

All probes below were executed live against the real endpoints from this
environment (`curl`, dated 2026-09-05) before any adapter code was written
against them, per the spec's "do not build on any endpoint until you have
hit it" instruction.

## Devpost (`https://devpost.com/api/hackathons`)

Base endpoint confirmed live, `meta.total_count = 13845` unfiltered.

| Query | Status | Effect |
|---|---|---|
| (none) | 200 | `total_count=13845`, 9 items/page (default `per_page`) |
| `?search=robotics` | 200 | `total_count=162` -- **server-side filtering works** |
| `?status[]=open` | 200 | `total_count=57`, all `open_state="open"` |
| `?page=2` | 200 | second page of the *unfiltered* set (13845 total) |
| `?search=robotics&status[]=open&page=1` | 200 | `total_count=4`, all open |
| `?search=robotics&status[]=upcoming` | 200 | `total_count=5`, all upcoming |
| `?search=robotics&per_page=50` | 200 | 40 items returned in one page (per_page respected, capped) |

**Finding: the WAF-403 note in `sources.yaml` no longer reproduces.** Every
combination above returned `200` with a `Referer: https://devpost.com/hackathons`
header and a descriptive `User-Agent`, tested individually and combined, with
~1s between requests. `search`, `status[]`, `page`, and `per_page` all filter
**server-side** and compose correctly. Phase 4b therefore uses server-side
`search=robot`-style queries plus `status[]=open`/`status[]=upcoming`
instead of fetching the full unfiltered 13,845-hackathon set and filtering
client-side. (The client-side `filter_keywords_any` code path is kept as a
defensive fallback in `adapters/json_adapter.py` in case the WAF behavior
described in the old notes returns intermittently -- it is cheap to keep and
costs nothing when the server-side filter already narrows the set.)

Sample hackathon record fields confirmed: `title`, `url`, `displayed_location.location`,
`open_state`, `submission_period_dates` (e.g. `"May 22 - Sep 13, 2026"`),
`prize_amount` (e.g. `"$<span data-currency-value>35,785</span>"`),
`registrations_count`, `themes[].name`.

## Challenge.gov

`https://www.challenge.gov/` returns `200` but is a client-rendered SPA shell
(hashed Angular-style asset bundle names, e.g. `styles-7JO7VBWO.css`) with no
challenge listing in the initial HTML and no visible XHR/fetch/API string
anywhere in that shell's markup. Probed and ruled out:

| Guess | Status |
|---|---|
| `/api/challenges` | 404 (after a 301 redirect to trailing slash) |
| `/wp-json/` | 404 (not WordPress) |
| `/feed/`, `/challenge/rss/` | 404 |

No `<meta name="generator">`, no `application/ld+json`, no `__NEXT_DATA__`/
`__NUXT__` payload found in the shell. This matches the spec's framing: "the
only public documentation that exists is a dead 2011 XML feed and an
abandoned third-party adapter." **No current JSON or XML feed was found.**
Per the spec's own contingency, `challenge_gov` is marked `disabled: true` in
`sources.yaml` with this note, rather than built against a guessed selector.

## HeroX

`https://www.herox.com/crowdsourcing-challenges` (the URL named in casual
reference) 404s -- the real listing page is `https://www.herox.com/crowdsourcing-projects`
(200). That page embeds a `<script id="page-data" type="application/json">`
blob whose `view.api_url` field names an internal JSON endpoint:

```
GET https://www.herox.com/async/api-internals/public/challenge/search
```

Confirmed live: `200`, returns `{"count": 677, "next": "...?page=2", "results": [...]}`
-- a real paginated JSON API, not an HTML scrape. Per-item fields confirmed:
`id`, `title`, `url`, `days_left`, `prize_short` (e.g. `"$3.1M"`), `promo`
(one-line description used as `snippet`), `creator_title`. A `?q=robotics`
query parameter was tested and did **not** change the result count or order
(677 either way) -- HeroX's search is not wired to this internal endpoint,
so filtering for this source is client-side keyword matching on
`title`+`promo`, the same pattern already used for the pre-probe Devpost
source. `herox` is added as `method: json` against this endpoint (`json_items_path: results`,
`filter_keywords_any` on robotics terms), not an HTML scraper.

## Grants.gov bulk XML

`https://www.grants.gov/xml-extract` is itself a JS-rendered Nuxt page, but
its rendered HTML embeds direct S3 links to the last several days of daily
full-database extracts, e.g.:

```
https://prod-grants-gov-chatbot.s3.amazonaws.com/extracts/GrantsDBExtract20260905v2.zip
```

Confirmed live: `200`, `Content-Length` (compressed) = 78,109,035 bytes
(~74.5 MiB). Unzipped: a single file `GrantsDBExtract20260905v2.xml`,
**uncompressed size 320,767,382 bytes (~306 MiB)**, containing ~heavily
nested `<OpportunitySynopsisDetail_1_0>` records (`OpportunityID`,
`OpportunityTitle`, `CloseDate` as `MMDDYYYY`, `Description`, `AgencyName`,
`EligibleApplicants`, `CFDANumbers`, etc.) under the
`http://apply.grants.gov/system/OpportunityDetail-V1.0` XML namespace.
A raw byte-level scan of one day's extract found ~375 occurrences of
"robot" (case-insensitive) across titles/descriptions -- there is real
robotics-adjacent signal in the bulk export, at very low density relative to
the full ~2700+ opportunities scanned in a partial pass.

Given the 300+ MB uncompressed size, `adapters/bulk_xml.py` streams the ZIP
entry directly into `xml.etree.ElementTree.iterparse` (never materializing
the full parsed tree, `elem.clear()` after each record) and discards non-matching
records immediately, so peak memory stays proportional to one record, not
the whole file. The current day's URL is discovered by re-fetching
`https://www.grants.gov/xml-extract` and taking the most recent dated
`.zip` link found there, rather than hardcoding today's date client-side
(the page lists the last several days, which tolerates the job running a
little early/late relative to when GSA publishes a given day's file).

---

## Phase 0c: calibration label set

**`OPENAI_API_KEY` is not set in this environment**, so per the spec's own
documented fallback, `scripts/label_corpus.py` uses **source-provenance
labeling** instead of a `gpt-5-nano` judge:

- Positive labels: items whose `source_id` is one of the robotics-native
  sources (`hackster`, `pcbway`, plus any Hackaday Prize / Hackster
  hardware-contest-tagged rows already in `state.db.bak`).
- Negative labels: items from `devpost` or `mlh` with no hardware-sounding
  theme/keyword signal.
- Everything else is excluded from the calibration set entirely (not
  labeled either way), since provenance alone can't safely call it.

`rules.yaml`'s `thresholds.relevance_floor` was set conservatively at the
value that admits **all** provenance-positive items (see the sweep table
appended below by `scripts/calibrate_floor.py`), rather than at the
precision-maximizing value a real judge would allow. **This floor should be
re-derived with the `gpt-5-nano` judge once `OPENAI_API_KEY` is available** --
provenance labels are a much weaker, more circular signal than an
independent judge, since "came from a robotics-native source" and "is
about robotics" are almost the same claim by construction. That circularity
is exactly the failure mode the spec's two-run-agreement / anchor-check
design exists to catch, and neither of those safeguards ran here.

### Known limitation

Per the spec: these labels (whether LLM-judged or, as here, provenance-derived)
are a second independent measurement, not external ground truth. Precision
numbers reported later in this plan (`docs/REBUILD_REPORT.md`) are precision
against this label set, not against verified real-world outcomes. The
provenance fallback is weaker than the two-independent-judge-run design the
spec describes for when a key is available, because "is from a robotics-native
source" and "is about robotics" overlap almost tautologically for the
positive class. This is a real limitation of the fallback path, not a solved
problem -- flagged here rather than hidden.

Inter-run judge disagreement rate: **not applicable** -- no `gpt-5-nano`
judge ran (no API key), so there is no second independent judging run to
disagree with itself. This line is included so the item is visibly addressed
rather than silently missing.

Anchor checks (robotics-native-source-positive-rate, Devpost/MLH-no-hardware-negative-rate):
**not run against an LLM judge** for the same reason. The provenance-fallback
labeling *is* the anchor-check logic in this run (by construction, every
provenance-positive item is from a robotics-native source and every
provenance-negative item is from Devpost/MLH with no hardware theme), so a
separate anchor-rate number would be circular/trivially 100% and is not
reported as if it were an independent check.

(Sweep table from `scripts/calibrate_floor.py` is appended below once Phase 2 runs.)

**`OPENAI_API_KEY` not set at run time** -- calibration labels were built via source-provenance fallback, not a gpt-5-nano judge. No inter-run disagreement rate or anchor rates were computed (they would be circular against provenance-only labels -- see the discussion above). 82 items labeled (16 positive, 66 negative), 97 excluded as not classifiable by provenance alone.

## Phase 2: relevance_floor sweep (scripts/calibrate_floor.py)

Swept against 82 labeled items from data/calibration_labels.json (recall constraint: >= 90%).

| floor | TP | FP | TN | FN | precision | recall | F1 |
|---|---|---|---|---|---|---|---|
| -4 | 16 | 1 | 65 | 0 | 0.94 | 1.00 | 0.97 |
| -3 | 16 | 1 | 65 | 0 | 0.94 | 1.00 | 0.97 |
| -2 | 16 | 1 | 65 | 0 | 0.94 | 1.00 | 0.97 |
| -1 | 16 | 1 | 65 | 0 | 0.94 | 1.00 | 0.97 |
| 0 | 16 | 1 | 65 | 0 | 0.94 | 1.00 | 0.97 |
| 1 | 16 | 1 | 65 | 0 | 0.94 | 1.00 | 0.97 |
| 2 | 16 | 1 | 65 | 0 | 0.94 | 1.00 | 0.97 |
| 3 | 16 | 1 | 65 | 0 | 0.94 | 1.00 | 0.97 |
| 4 | 16 | 1 | 65 | 0 | 0.94 | 1.00 | 0.97 | **<- selected**
| 5 | 15 | 1 | 65 | 1 | 0.94 | 0.94 | 0.94 |
| 6 | 13 | 1 | 65 | 3 | 0.93 | 0.81 | 0.87 |
| 7 | 13 | 1 | 65 | 3 | 0.93 | 0.81 | 0.87 |
| 8 | 7 | 1 | 65 | 9 | 0.88 | 0.44 | 0.58 |
| 9 | 7 | 0 | 66 | 9 | 1.00 | 0.44 | 0.61 |
| 10 | 7 | 0 | 66 | 9 | 1.00 | 0.44 | 0.61 |
| 11 | 3 | 0 | 66 | 13 | 1.00 | 0.19 | 0.32 |
| 12 | 3 | 0 | 66 | 13 | 1.00 | 0.19 | 0.32 |
| 13 | 3 | 0 | 66 | 13 | 1.00 | 0.19 | 0.32 |
| 14 | 1 | 0 | 66 | 15 | 1.00 | 0.06 | 0.12 |
| 15 | 1 | 0 | 66 | 15 | 1.00 | 0.06 | 0.12 |
| 16 | 1 | 0 | 66 | 15 | 1.00 | 0.06 | 0.12 |
| 17 | 1 | 0 | 66 | 15 | 1.00 | 0.06 | 0.12 |
| 18 | 1 | 0 | 66 | 15 | 1.00 | 0.06 | 0.12 |
| 19 | 1 | 0 | 66 | 15 | 1.00 | 0.06 | 0.12 |
| 20 | 1 | 0 | 66 | 15 | 1.00 | 0.06 | 0.12 |
| 21 | 1 | 0 | 66 | 15 | 1.00 | 0.06 | 0.12 |
| 22 | 1 | 0 | 66 | 15 | 1.00 | 0.06 | 0.12 |

Selected floor **4**: maximizes precision (0.94) subject to recall >= 90% (achieved recall 1.00).

