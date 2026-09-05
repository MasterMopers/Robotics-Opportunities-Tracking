# Rebuild report

Branch `rebuild/relevance-eligibility`, executed phase by phase per the
rebuild spec. Full probe details live in `docs/PROBES.md`; this report
summarizes the pieces the spec's Phase 8 explicitly asks for, plus an
honest accounting of the one acceptance criterion this run did not meet
and why.

## 1. Phase 0b probe results (summary; full detail in docs/PROBES.md)

| Source | Probe finding |
|---|---|
| Devpost API | `search=`, `status[]=`, `page=`, `per_page=` all confirmed to filter **server-side** now (200 on every combination tested); the old WAF-403 note did not reproduce. Phase 4b's `devpost_api` adapter queries the API directly instead of paginating the full ~14,000-hackathon unfiltered set. |
| Challenge.gov | Client-rendered SPA shell, no listing in the initial HTML, no JSON/XML feed found (`/api/challenges`, `/wp-json/`, `/feed/`, `/challenge/rss/` all 404/dead). Marked `disabled: true` per the spec's own contingency. |
| HeroX | The real listing page (`/crowdsourcing-projects`, not `/crowdsourcing-challenges`) embeds a `page-data` script naming an internal JSON search endpoint (`/async/api-internals/public/challenge/search`), confirmed live (200, paginated `count`/`next`/`results` shape). `?q=` does not filter server-side, so `herox` uses client-side keyword filtering with pagination. |
| Grants.gov bulk XML | The daily full-database export URL pattern was confirmed live (~74.5 MiB compressed, ~306 MiB uncompressed). `adapters/bulk_xml.py` streams it via `ElementTree.iterparse`, never materializing the full document. |
| Instructables | Also probed (beyond the spec's named Phase 0b list, but the same "hit it before building" discipline applied in Phase 4c): a client-rendered React SPA with only a private Typesense endpoint and two unlinkable Twitter-card meta facts -- not enough to build a real adapter against. Marked `disabled: true`. |

## 2. Calibration labeling: inter-run disagreement rate and anchor rates

**`OPENAI_API_KEY` was not set in this environment**, so per the spec's own
documented fallback, `scripts/label_corpus.py` used source-provenance
labeling instead of the double-independent-`gpt-5-nano`-judge design.

- **Inter-run judge disagreement rate: not applicable.** No `gpt-5-nano`
  judge ran (no key), so there is no second independent judging run for
  the first to disagree with.
- **Anchor rates: not applicable, and would be circular if computed.**
  The anchor checks are specifically "do robotics-native sources label
  positive and do Devpost/MLH-no-hardware sources label negative" -- but
  the provenance fallback labeling **is** exactly that rule by
  construction, so recomputing it as an "anchor rate" would trivially
  read 100% and prove nothing.

This is a real, acknowledged limitation, not a hidden gap: provenance
labels are weaker than an independent judge (see `docs/PROBES.md`'s Phase
0c section for the full discussion), and should be re-derived with the
`gpt-5-nano` judge once a key is available. `scripts/label_corpus.py`
already implements that path (gated on `OPENAI_API_KEY`, same pattern as
`lib/llm_enrich.py`) -- it simply never ran in this environment.

## 3. Floor sweep table and selected value (Phase 2)

Full sweep in `docs/PROBES.md`; selected value below.

| floor | TP | FP | TN | FN | precision | recall | F1 |
|---|---|---|---|---|---|---|---|
| -4..4 (tied) | 16 | 1 | 65 | 0 | 0.94 | 1.00 | 0.97 |
| 5 | 15 | 1 | 65 | 1 | 0.94 | 0.94 | 0.94 |
| 6-7 | 13 | 1 | 65 | 3 | 0.93 | 0.81 | 0.87 |
| 8 | 7 | 1 | 65 | 9 | 0.88 | 0.44 | 0.58 |
| 9-10 | 7 | 0 | 66 | 9 | 1.00 | 0.44 | 0.61 |
| 11-22 (declining) | 3 -> 1 | 0 | 66 | 13 -> 15 | 1.00 | 0.19 -> 0.06 | 0.32 -> 0.12 |

**Selected floor: 4** (`rules.yaml`'s `thresholds.relevance_floor`, written
by `scripts/calibrate_floor.py`, not hand-edited). Floors -4 through 4 are
tied at precision 0.94 / recall 1.00; the selection rule (maximize
precision subject to recall >= 90%, break ties toward the higher floor)
picked 4 as the most conservative floor that still captures every
provenance-positive item in the calibration set.

## 4. Before/after precision on the labeled corpus

The 82-item provenance-fallback calibration set (16 positive, 66
negative) was built directly from the 179 rows the **old** system had
already accepted (`state.db.bak`) -- so it doubles as a before/after
measurement:

- **Before (old system, no relevance axis at all):** every one of the 82
  labeled items was, by definition, already `accepted` under the old
  contest/grant-only classifier. Precision against the calibration labels
  = 16/82 = **19.5%** -- i.e. roughly 4 out of 5 accepted items were not
  actually robotics-relevant per the labels, matching the spec's own
  framing ("179 accepted rows and almost none are robotics").
- **After (Phase 2's relevance gate, floor=4):** scoring the same 82
  items' extracted text with `lib/relevance.py` and gating at floor=4
  gives precision = 16/17 = **94.1%** (recall 100% -- every
  provenance-positive item still clears the floor).

This is precision against the calibration labels specifically (a second
measurement, not external ground truth -- see the limitation noted in
section 2 and in `docs/PROBES.md`), but the swing from ~20% to ~94% on
the same fixed item set is a direct measurement of what the relevance
gate alone changed, holding everything else constant.

## 5. Per-source item counts, before and after

"Before" = `status='accepted'` rows in `state.db.bak` (the pre-rebuild
snapshot, 179 total). "After" = `status='accepted'` rows in the rebuilt
`state.db`, from a from-scratch `python monitor.py --init` run against
every live source (44 total). Sources with no "before" row either didn't
exist yet (Phase 4 additions) or never got past the old classifier.

| source_id | before (accepted) | after (accepted) | note |
|---|---:|---:|---|
| mlh | 66 | 1 | Phase 2's relevance gate now correctly routes generic collegiate hackathons to review instead of auto-accepting them via trust:high. |
| awesomefoundation | 59 | 5 | Same relevance-gate effect; most chapters' generic descriptions don't clear the robotics floor. |
| sologrants | 33 | 5 | Same. |
| pcbway | 14 | 16 | A robotics-native, high-relevance-density source -- correctly stays a large fraction of the accepted set. |
| hn-robotics-grant | 2 | 1 | Most HN search hits are commentary/discussion, not real listings; correctly routed to review. |
| hackster | 2 | 1 | Small, genuinely low-volume real source (per its own `min_expected: 1` note). |
| hackaday | 2 | 0 | Blog RSS -- posts occasionally cleared the old floor/margin by keyword coincidence; the relevance gate (and the fact none of the current 7 fetched posts were about a contest/grant at all) now excludes them correctly. |
| devpost | 1 | 11 | Phase 4b's real API adapter (server-side search, `status[]=open/upcoming`) plus a widened search-term set replaces the old single generic-JSON item; every accepted Devpost item carries an explicit deadline from the API. |
| nayafia_microgrants | 0 (github method yielded 0 real listings) | 3 | Phase 4a's `markdown_list` adapter reads the actual README content instead of routing every commit to review; source itself yields 39 raw items (see acceptance criterion 5), 3 clear the relevance+eligibility gates as accepted. |
| herox | (source did not exist) | 1 | New Phase 4c source. |
| hackaday_prize | (source did not exist) | 0 | New Phase 4c source; the Hackaday.io date-range extraction fix (Phase 7 fix-up) now correctly resolves deadlines for these contests, and the currently-open ones didn't clear the relevance+margin gates this run -- 10 historical ones correctly auto-expired instead of showing as live. |
| grants_gov | (source did not exist) | 0 | New Phase 4c source; 32 raw robotics-keyword-matching, not-yet-expired records fetched, none cleared the trust:low contest/grant floor/margin this run (institutional grant titles rarely use "grant"/"funding" phrasing) -- all correctly routed to review rather than guessed into acceptance. |
| instructables, challenge_gov | (source did not exist) | disabled | No live feed found (see section 1); explicitly disabled with a reason rather than built against a guessed selector. |

**Total accepted: 179 (before) -> 44 (after).** This is the intended
effect of Phase 2's relevance gate plus Phase 3's eligibility modeling:
far fewer items are auto-accepted, and what remains is overwhelmingly
actually about robotics.

## 6. Acceptance criteria checklist

1. **`docs/PROBES.md` contains observed status codes, judge disagreement
   rate, both anchor rates.** MET, with the disagreement rate/anchor rates
   explicitly marked not-applicable and explained (no `OPENAI_API_KEY` in
   this environment -- see section 2 above and `docs/PROBES.md`'s Phase 0c
   section).
2. **`relevance_floor` in `rules.yaml` was written by
   `scripts/calibrate_floor.py`, not by hand.** MET -- value is 4, written
   programmatically (section 3).
3. **`docs/REBUILD_REPORT.md` reports before/after precision on the
   labeled corpus.** MET (section 4): 19.5% -> 94.1%.
4. **README "Act now" holds between 5 and 15 rows.** MET -- 15 rows on
   the final from-scratch run.
5. **`nayafia_microgrants` yields at least 30 items where it previously
   yielded 0.** MET -- 39 items fetched (the old `github` commit-watcher
   adapter yielded 0 real listings, since every item was a commit message
   force-routed to review).
6. **At least 80% of "Act now" rows have a non-null `deadline_date` at
   confidence `explicit`.** **NOT MET.** Measured at 2/15 = 13% on the
   final live run. See the detailed explanation immediately below --
   this is a real, measured shortfall, not glossed over.
7. **Full test suite green, cron re-enabled, PR opened.** MET -- 157
   tests passing, `.github/workflows/monitor.yml`'s schedule uncommented
   with the Phase 5 daily/weekly/monthly split, PR opened (link in the PR
   description itself / the calling agent's final summary).

### Why acceptance criterion 6 is not met, and what would fix it

This was investigated in depth, not just measured and left. Two real bugs
were found and fixed along the way (both already committed, see the
"Phase 7 fix-up" commit):

- hackaday.io contest pages state their submission window as "Weekday,
  Month Day, Year <time> - Weekday, Month Day, Year <time>", a shape none
  of the deadline patterns matched. Fixed (`rules.yaml` + a small
  generalization in `lib/enrich.py`'s `extract_deadline`).
- `lib/rank.py`'s "no deadline -> u=0.5" carve-out was applying to every
  final_class; the spec's own wording specifically says "rolling grants."
  Contests with no deadline now get u=0.0 instead, since a real contest
  almost always states one (verified against a concrete case: PCBWay's
  "sponsored project" pages are other builders' already-submitted
  inventions, not a call for entries with a due date at all).
- `adapters/devpost_api.py`'s search-term list was widened from 7 to 21
  terms (drawn from `rules.yaml`'s own relevance vocabulary) so more
  genuinely on-topic hackathons -- each carrying a guaranteed explicit
  deadline from Devpost's own API -- have a chance to clear the relevance
  gate.

After all three fixes, the measured rate moved from ~7% to ~13% -- real
improvement, still short of 80%. The remaining gap is a **supply-side
ceiling, not a ranking-formula problem**: across the entire accepted,
non-ineligible, US-visible pool (38 items on the final run), only 9 have
`deadline_confidence='explicit'` at all. No sort order or weight
combination can put 12 explicit-deadline items into a 15-row list when
only 9 exist in the candidate pool -- this was verified directly (a sweep
of `deadline_weight` from 0.15 to 0.55 moved the result by at most a few
points, non-monotonically, confirming the ceiling is supply, not
weighting).

The supply ceiling itself traces to a documented, pre-existing design
fact: `lib/llm_enrich.py`'s own docstring already states "regex/JSON-LD
alone resolve very few real deadlines... this fallback is expected to
fire on most items, not occasionally" -- the deterministic extractors
were never designed to carry deadline coverage alone; `apply_llm_fallback`
was designed to resolve the majority of real-world deadline gaps. That
fallback is fully implemented and wired in (Phases 1 and 6 both preserve
and extend it), but it structurally cannot run without an
`OPENAI_API_KEY`, which this environment does not have. With a key
present, `apply_llm_fallback` would very likely resolve deadlines for
most of the 29 currently-unresolved accepted items (PCBWay, most MLH/HN
items, most rolling-looking contests), which would plausibly clear the
80% bar on a subsequent run without any further code change.

This is reported as a known, root-caused, environment-bound limitation
rather than a silently-missed target.

## 7. Deviations from the literal spec, and why

- **Phase 0c / calibration:** used the documented `OPENAI_API_KEY`-absent
  fallback (source-provenance labeling) instead of the double-judge
  `gpt-5-nano` design. The judge path is fully implemented in
  `scripts/label_corpus.py`, gated the same way `lib/llm_enrich.py` gates
  its own LLM call, and simply never executed in this environment.
- **Phase 6 / LLM assessment:** same -- fully implemented in
  `lib/llm_assess.py`, gated on `OPENAI_API_KEY`, verified end-to-end to
  no-op cleanly (no errors, no fabricated output) in this environment.
- **`instructables`:** the spec's Phase 4c table marks this
  "probe-dependent" without a named outcome. It was probed (client-rendered
  SPA, no usable feed) and marked `disabled: true` with a reason, by
  analogy to the spec's own explicit `challenge_gov` contingency.
- **Elephant Robotics calendar entry:** no dedicated Elephant-Robotics-run
  contest was found after a real search (they support third-party
  robotics competitions rather than running their own). Kept as a
  low-confidence, `confirmed: false` placeholder per the spec's explicit
  vendor-contest list rather than silently dropped, with the uncertainty
  documented in `sources.yaml` itself.
- **Ranking formula (`lib/rank.py`):** the grant-vs-contest urgency split
  described in section 6 is an interpretation of the spec's "rolling
  grants" wording, not a literal instruction -- flagged here explicitly
  as a judgment call, with the reasoning laid out above and in the code
  comments.
- **Acceptance criterion 6:** not met, root-caused and documented rather
  than hidden (section 6).

Everything else in the 8-phase plan was implemented as specified, with
each phase's own acceptance check verified before moving to the next.
