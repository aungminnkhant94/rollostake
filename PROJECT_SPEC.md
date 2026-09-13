# RolloStake Project Specification

**Version:** 0.2
**Status:** Initial evidence-based baseline
**Baseline date:** 2026-09-03
**Implementation update:** 2026-09-11 — automated UEFA context import and audited weekly publication
**Scope:** Production weekly prediction workflow, persistent data, official cards,
settlement, dashboard, and isolated research tooling.

## 1. Document purpose and evidence policy

This document defines what RolloStake is expected to do and records the current
implementation boundary. It is based on the repository's code, configuration,
tests, database schema, and operating handoffs as inspected on the baseline
date.

Evidence labels used below:

- **Implemented**: directly present in current code or schema.
- **Required**: an intentional operating or product rule stated by the project
  handoff and accepted as part of this specification.
- **Gap**: required or desirable behavior that current code does not fully
  enforce.

When sources conflict, current code and tests describe actual behavior, while
this specification describes intended behavior. A material intentional change
must update this file, the implementation, and the relevant tests together.
Generated `dashboard/index.html` must not be treated as the implementation
source; persistent dashboard changes belong in `dashboard/generator.py`.

Primary evidence:

- `config/settings.json` and `config/settings.py` — active runtime policy.
- `models/core.py` — canonical SQLite schema and migrations.
- `main.py` — model and prediction orchestration.
- `analysis/adjustment_layers.py` — context adjustments and evidence states.
- `analysis/edge_calculator.py` — pricing, learning, gates, and single-pick
  selection.
- `scripts/rebuild_card.py` and `dashboard/generator.py` — official card,
  parlays, and dashboard generation.
- `scripts/import_match_results.py` and `utils/match_resolver.py` — guarded
  settlement and time handling.
- `WEEKLY_AGENT_HANDOFF.md` and `AGENTS.md` — approved operating contract.
- `tests/test_*.py` — currently automated safeguards.

`README.md` contains useful historical background, but parts of its status and
"What's Missing" sections predate the current range, settlement, Parley, and
context-evidence implementations. It is not authoritative where it conflicts
with the sources above.

## 2. Product definition

RolloStake is a local football prediction and staking decision-support system.
It fits league-specific Dixon-Coles models, adjusts their expected-goal rates
with auditable football context, compares model probabilities with available
odds, selects constrained weekly cards, learns from settled results, and
renders a static dashboard.

The primary user outcome is a smaller, better-supported weekly card whose
results can be settled and evaluated honestly over time.

RolloStake does not:

- place wagers or connect to an exchange for execution;
- guarantee wins or treat a model edge as proof of profit;
- copy a friend's selections as official picks;
- use unsupported quarter Asian-handicap lines;
- fabricate unresolved fixtures during the normal odds workflow;
- claim performance improvement before the relevant picks have settled; or
- promote research configurations into production automatically.

## 3. Success criteria

### 3.1 Process success

A weekly cycle is operationally successful when it:

1. settles only verified, completed matches;
2. measures Low Risk, High Risk, and Parley separately and records an
   evidence-backed learning decision;
3. waits for every relevant midweek UEFA match involving a coming-fixture team
   to finish, then refreshes its player, injury, discipline, rotation, fatigue,
   travel, and rest effects;
4. refreshes fixtures, squad depth, team news, predictions, and odds in the
   required order;
5. rejects ineligible or weakly supported candidates instead of filling a
   quota;
6. produces auditable singles and Parley records;
7. rebuilds a usable desktop and mobile dashboard; and
8. passes the validation contract in section 12.

### 3.2 Performance success

Performance improvement is proven only by settled evidence. Low Risk, High
Risk, and Parley must be measured separately using:

- wins, losses, and pushes;
- hit rate over decisions;
- total staked;
- profit and loss;
- return on amount staked; and
- live bank after settled P&L.

More picks, changed model parameters, or improved backtest results alone do not
prove live improvement.

## 4. Supported production scope

### 4.1 Leagues

The configured core leagues are:

- English Premier League (`EPL`)
- French Ligue 1 (`L1`)
- German Bundesliga (`Bundesliga`)
- Italian Serie A (`SerieA`)
- Spanish La Liga (`LaLiga`)

### 4.2 Official market families

The official card may use:

- `1X2` — home win, draw, or away win;
- `OU` — match totals;
- `BTTS` — both teams to score;
- `TT` — team totals; and
- `AH` — supported Asian handicaps, including draw-no-bet style selections.

Whole and half lines are supported by the single-result settlement model.
Whole lines can settle as a push. Any line requiring split half-win or
half-loss accounting must be excluded; the current importer explicitly rejects
quarter Asian handicaps.

## 5. System architecture and data flow

The production flow is:

```text
historical completed matches ──> league Dixon-Coles fit
                                      │
ESPN fixtures ────────────────────────┤
current squads + team news + context ─> 14-layer lambda adjustment
                                      │
Polymarket odds ──────────────────────> market probabilities and edge
                                      │
settled band history + friend profile ─> ranking and loss-trap filters
                                      │
                                      ├──> High Risk singles
                                      ├──> Low Risk singles
                                      └──> separate Parley slips
                                               │
SQLite audit trail ────────────────────────────> generated HTML dashboard
```

`data/rollo_stake.db` is the operational source of truth. CSV and JSON files
are inputs or portable evidence, not substitutes for the settled database.

## 6. Data contract

`models/core.py` owns schema initialization and backward-compatible column
migrations. The persistent entities are:

| Entity | Purpose |
|---|---|
| `matches` | Fixtures, kickoff, league, score, status, and fatigue summary. |
| `odds` | Bookmaker market selections, decimal prices, implied probability, and collection time. |
| `predictions` | One current model prediction per match, including lambdas and market probabilities. |
| `prediction_adjustment_layers` | Per-match, per-layer before/after lambdas, notes, activity, and evidence state. |
| `picks` | Official single selections, band, price, probability, edge, stake, status, and denormalized settlement. |
| `results` | Auditable settled single-pick history and P&L. |
| `bankroll` | Optional weekly bankroll snapshots; live band banks are currently derived from configured bank plus settled results. |
| `team_news` | Current injury/suspension context and source metadata. |
| `squad_players` | Current player-level roster snapshot. |
| `squad_depth` | Per-team positional counts and roster coverage. |
| `parley_slips` | Separate multi-leg stake, price, probability, status, and P&L. |
| `parley_legs` | Ordered selections and leg-level settlement for each Parley. |

Repeated prediction runs must leave only one prediction row per `match_id`.
All cross-source match resolution must normalize team names and prefer an
existing fixture ID.

Kickoff strings may be local naive timestamps or UTC ISO timestamps.
`utils.match_resolver.parse_kickoff_utc()` and
`format_kickoff_local()` are the required conversion boundary. User-facing
times and played dates must use `Asia/Macau` (UTC+8).

## 7. Ingestion and model requirements

### 7.1 Fixtures

**Implemented:** `scripts/fetch_weekly_fixtures.py` retrieves the configured
window from ESPN's token-free `site.web.api.espn.com` scoreboard API, maps the
five league codes, normalizes teams, and stores scheduled fixtures. A refresh
may mark fixtures in the same window stale before saving the new slate.

**Required:** The normal weekly window is seven days. Fixture records must be
real, future-dated, normalized, and within the requested leagues and dates.
The stored slate must also remain inside the requested Macau-local date window
after ESPN's UTC kickoff timestamp is converted.

### 7.1a European observation

**Implemented:** `scripts/import_uefa_context.py` compares the coming domestic
team pool with the current Champions League, Europa League, and Conference
League scoreboards. It fails closed when a relevant match is unresolved. For
completed relevant matches it stores normalized match rows, writes
`data/uefa_context_weekly.json`, and refreshes each coming team's European
schedule and rotation-review evidence in `data/team_context.json`.

**Required:** Run the command in dry-run mode before the live import. Review
the saved scores, starters, substitutions and minutes, injuries, discipline,
extra time, travel, rest, and material performance evidence before fitting.

### 7.2 Squads and positional depth

**Implemented:** `scripts/fetch_squad_depth.py` fetches ESPN team rosters,
requires a configurable minimum player count, validates all requested clubs,
and replaces the current league snapshots in a transaction only after the
complete fetch succeeds.

**Required:** Run `--dry-run` before the write. Squad membership is context,
not an automatic positive adjustment. Player positions may be used to classify
injury impact.

### 7.3 Team news

**Implemented:** `scrapers/browser_news_scraper.py` supports browser-sourced
injury data and a manual JSON fallback. A successful live refresh deduplicates
rows and replaces the current feed rather than appending another copy.

**Required:** Run a dry run first and fail closed on incomplete or empty source
coverage. A team-news feed is considered fresh by the adjustment engine only
when its newest row is no more than seven days old relative to the fixture.

### 7.4 Model fitting

**Implemented:** `main.py` fits one SciPy/NumPy Dixon-Coles model per league
using completed historical matches involving the upcoming teams. It then
predicts each scheduled fixture and persists the adjusted output and layer
audit.

**Required:** Production cards must be based on real historical data and a
successful league fit. Demo fixtures or default, unfitted model parameters are
compatibility fallbacks, not acceptable evidence for a staking-ready card.

### 7.5 Fourteen adjustment layers

Expected-goal lambdas pass through, in order:

1. rolling-form blend;
2. Elo strength of schedule;
3. finishing quality / xG proxy;
4. motivation;
5. manager bounce;
6. derby;
7. injuries and suspensions;
8. European competition fatigue;
9. cup fatigue;
10. rest days;
11. rotation;
12. luck regression;
13. lambda cap; and
14. Dixon-Coles low-score correction.

The configured lambda interval is `[0.30, 5.00]` and the configured
Dixon-Coles correlation parameter is `rho = -0.13`.

Each audit row must use one of these evidence states:

- `ACTIVE` — evidence caused an adjustment;
- `NO_SIGNAL` — evidence was available but caused no adjustment;
- `NOT_APPLICABLE` — the layer does not apply to the fixture; or
- `MISSING_DATA` — the necessary evidence was unavailable.

Legacy or absent states are treated as `UNKNOWN` by the gate.

## 8. Odds and pricing requirements

**Implemented:** `scripts/scrape_polymarket_full.py` discovers parent match
events and additional supported markets, converts probabilities to decimal
odds, normalizes teams, resolves to existing fixtures, rejects quarter
handicaps, and supports a no-write dry run. Existing Polymarket rows for an
imported selection can be overwritten to refresh the price.

**Required:**

- Dry-run before every live odds import.
- Continue only when unresolved matches are understood, `bad_rows` is zero,
  and no unintended fixture would be created.
- Do not use `--create-missing` without explicit user direction.
- Official selections must use fresh odds tied to the exact fixture.
- Edge is `model probability - (1 / decimal odds)`.
- Prices and selections must be preserved exactly enough for correct market
  settlement.

**Gap:** Candidate generation reads the stored price but does not currently
filter on `odds.scraped_at`. Odds freshness is therefore an operating audit,
not a fully enforced code gate.

## 9. Official single-pick contract

The active configuration uses flat staking and two independent risk bands:

| Rule | High Risk (`C`) | Low Risk (`D`) |
|---|---:|---:|
| Starting bank | $100 | $100 |
| Flat stake | $10 | $10 |
| Decimal odds | 2.50–5.00 | 1.70–2.70 |
| Minimum edge | 10% | 10% |
| Minimum model probability | none beyond other gates | 55% |
| Maximum picks | 8 | 10 |
| Maximum per match | 1 | 1 |
| Maximum exposure family per match | 1 | 1 |
| Market caps | 1X2: 4, AH: 4, OU: 2 | 1X2: 2, AH: 4 |

Both bands allow `1X2`, `OU`, `BTTS`, `TT`, and `AH`, subject to their caps.
Both allow home, away, over, and under selection types; draw selections are not
currently allowed by the configured selection-type list.

Quality labels are based on raw edge:

- `STRONG`: at least 25%;
- `KEEP`: at least 10% and below 25%;
- `CAUTION`: at least 5% and below 10%;
- `SKIP`: below 5%.

Because both active bands require at least 10% edge and explicitly exclude
`SKIP`, a current official single should be `KEEP` or `STRONG`.

Every official single must:

1. belong to a scheduled match whose parsed kickoff is still in the future;
2. use the configured bookmaker and an allowed market, price, and selection
   type;
3. satisfy the band probability, edge, count, match, and exposure limits;
4. have no missing required context evidence;
5. avoid learned loss traps and hard-coded repeated-loss shapes; and
6. be affordable from the band's live bank.

The context gate requires layers 1, 2, 7, and 10 for every market. `OU`, `TT`,
and `BTTS` additionally require layer 3. `MISSING_DATA`, `UNKNOWN`, an absent
row, or an unavailable audit schema blocks the candidate; `ACTIVE`,
`NO_SIGNAL`, and `NOT_APPLICABLE` are usable evidence states.

The live bank is starting bank plus settled P&L for that band. If the bank is
below one flat stake, the band is paused. A refresh never deletes, supersedes,
or rewrites a pending pick. Existing pending picks remain active until normal
settlement. New picks use different matches and fill only the remaining slots
under the band's total card ceiling.

### 9.1 Learning and loss traps

Own settled results are the primary learning evidence and remain separated by
risk band. The selector may adjust ranking using band-specific performance by
quality, market, exposure family, selection type, goal/handicap line,
market-plus-line, and odds bucket. Minimum sample sizes and bounded adjustments
must prevent a single result from dominating ranking.

For settled official picks that preserve their original model probability, the
selector also performs downside-only market calibration. A market needs at
least ten decisions, negative ROI, and a material predicted-versus-realized
gap before its probability and edge are reduced. Profitable markets are never
boosted by this step.

Repeatedly losing segments are excluded from official selection and cannot be
reintroduced as filler. The hard-loss rules additionally include:

- Low Risk goal markets contradicted by at least two context downgrades that
  outnumber supports;
- the learned Low Risk team-total Under 1.5 trap, kept distinct from match-total
  Under 1.5;
- High Risk low-total shapes supported by losing history;
- High Risk Under 1.5 selections;
- High Risk Asian handicaps of `-1.5` or more aggressive; and
- unsupported High Risk away 1X2 selections when context does not support the
  side.

Friend cards are secondary evidence. `scripts/study_external_card.py` reduces
them to an aggregate market-structure profile. The profile may apply a small
ranking prior but must never import or copy exact friend selections, and own
settled loss traps override it.

## 10. Parley contract

Parley is a separate product track, not another display of Low Risk singles.
It has its own persisted slips, legs, settlement, history, and P&L.

Candidate requirements are:

- decimal odds from 1.25 through 2.70;
- model probability of at least 54%;
- edge of at least 3%;
- complete context evidence; and
- no hard-loss trap from either Low Risk or High Risk history.

Candidate odds bands are:

- below 1.70: `LOW ODDS`;
- 1.70 through 2.15: `LOW RISK`;
- above 2.15 through 2.70: `BOOSTER`.

Ranking weights settled Low Risk learning at 65% and High Risk learning at 35%,
then rewards lower odds and penalizes high or booster odds. Only one leg per
match is allowed.

The generator attempts to save:

- one conservative two-leg slip with no booster; and
- one balanced three-leg slip with at most one booster.

A slip is omitted unless its full two or three eligible legs exist. The current Parley
stake is half the global flat stake, with a minimum of $1; under the active
configuration it is $5.

**Implemented September 5 remediation:** Existing pending slips are preserved.
New slips do not share matches with pending exposure, newly proposed singles,
or each other. Pending Parley stakes reserve bank before new slips are funded.
The reviewed rebuild excludes new singles on pending single or Parley matches.

### 10.1 Draft review and publication

`main.py` defaults to prediction-only computation. `scripts/rebuild_card.py`
requires either `--preview PATH` or `--publish-reviewed PATH`. Preview writes
only its JSON artifact. Publication recalculates and compares the exact draft
and database/code/settings fingerprint, rejects blockers, then saves the card.
Exact-market odds and predictions must be at most 6 hours old, and all new
kickoffs must be future and within seven days. The agent reviews football
evidence before executing publication; the CLI cannot itself verify that a
human or model read the evidence. Previously started recommendations remain
recorded even when refreshed inputs would reject them.

### 10.2 Probability and evidence corrections

Both production BTTS paths sum the unconditional probability of both teams
scoring; 0-0 remains in the distribution as a losing outcome. Match history is
normalized and duplicate fixtures reconciled when read, so historical team
aliases do not split Inter or double-count overlapping ESPN history. Team
news uses explicit player aliases, reconciles latest evidence before filtering
injury status, and retains original manual evidence dates. Injury freshness is
checked per team rather than using the newest row anywhere in the feed. A
missing team's feed is not evidence of a healthy squad.

## 11. Settlement and reporting contract

`match_results.csv` is the portable result input. A row must resolve to an
existing scheduled or stale fixture. Settlement is refused until 105 minutes
after parsed kickoff. Final scores, rather than a manually asserted overall
result, should determine each market outcome.

The settlement engine must correctly handle win, loss, and push for `1X2`,
draw-no-bet, `BTTS`, match totals, team totals, and supported Asian handicaps.
It updates the match, denormalized pick fields, and the `results` audit row in
one database transaction. Duplicate home/away fixtures within three days of
the resolved kickoff may be marked together to reconcile source IDs.

A Parley leg settles from the same market rules. One losing leg settles the
whole slip immediately as an irreversible loss. Otherwise the slip waits until
every leg is a win or push; pushed legs contribute odds of 1.0.

Settled history must display the match's played/kickoff date in Macau time, not
the row's import or settlement timestamp.

## 12. Dashboard and validation contract

### 12.1 Dashboard

`dashboard/generator.py` must generate `dashboard/index.html` with tabs ordered:

1. Low Risk;
2. High Risk; and
3. Parley.

The dashboard must show pending cards, exact market selections, Macau kickoff
times, odds, model probabilities, edges, stakes, quality, reasoning, risk
notes, separate band and Parley performance, settled histories, and fixture
odds coverage. The uncovered-fixture table uses 10 rows per page. Layout must
remain usable on desktop and mobile.

The dashboard's WIN/LOSS/PUSH controls use browser `localStorage` for a visual
preview only. They do not write authoritative settlements to SQLite. Official
results must go through the guarded import/settlement path.

### 12.2 Required weekly sequence

The production cycle has three separately authorized stages. A results and
learning review verifies and settles completed matches, reports the three tracks
separately, analyzes performance and calibration by meaningful segment, and
records whether evidence supports a change. It does not generate picks.

For the prediction stage, first fetch the coming domestic fixture slate. Before
model fitting, odds import, or card creation, identify every team in that slate
that played in the intervening UEFA Champions League, Europa League, or
Conference League round. Every relevant match must be final. Review the score,
lineup, substitutions and player minutes, injuries, suspensions and red cards,
rotation, extra time, travel, rest, and material performance evidence, then
refresh the affected squad/news/context inputs. A live, postponed, abandoned,
or unresolved relevant match blocks the prediction and publication stages.

When all relevant European matches are final and the user has requested picks,
the production sequence is:

```powershell
git status -sb
python scripts\import_match_results.py match_results.csv
python scripts\study_external_card.py friend_cards
python scripts\fetch_weekly_fixtures.py --days 7
python scripts\import_uefa_context.py --dry-run
python scripts\import_uefa_context.py
python scripts\fetch_squad_depth.py --dry-run
python scripts\fetch_squad_depth.py
python scrapers\browser_news_scraper.py --dry-run
python scrapers\browser_news_scraper.py
python main.py --skip-scrape --no-fatigue --predictions-only
python scripts\scrape_polymarket_full.py --days 7 --dry-run
python scripts\scrape_polymarket_full.py --days 7
python scripts\rebuild_card.py --preview output\card-review.json
```

After Codex's evidence review and zero publication blockers:

```powershell
python scripts\rebuild_card.py --publish-reviewed output\card-review.json
python scripts\adjustment_layer_report.py
python scripts\news_impact_report.py
```

For the shorter `update` workflow, if two or more official picks remain pending
after settlement, study the friend cards and rebuild the dashboard/card without
refreshing the next slate. If fewer than two remain, run the full refresh path.

### 12.3 Validation gates

Before a weekly result is reported as complete:

- run `python -m unittest discover -s tests -p 'test_*.py'`;
- compile every touched Python file;
- run `git diff --check`;
- run SQLite `PRAGMA quick_check`;
- inspect context and news reports for the final picks;
- audit every saved selection against section 9;
- verify no unintended or fake fixture was created; and
- serve the dashboard through localhost and inspect desktop and mobile views.

The final report must list singles and Parlays separately and include Macau
kickoff, market meaning, odds, model probability, edge, and stake. It must also
report each track's record, P&L, ROI, bank, pending count, blocked candidates,
new learning, the evidence-backed keep/change decision, the relevant European
matches reviewed and their material effects, changes from the previous week,
and whether settled performance actually improved.

## 13. Research boundary

The `research/` subsystem runs rolling historical experiments over range
configuration, edge thresholds, market mix, and exposure caps. It can use the
production SciPy/NumPy model or the experimental PyTorch/GPU model. Generated
caches, leaderboards, reports, and agent worktrees are research artifacts.

Research must not update the live SQLite database, current picks, settled
results, or dashboard. A configuration or model can enter production only
after review and credible holdout evidence, followed by an explicit code,
configuration, test, and specification change.

## 14. Known gaps and limitations

1. Stored odds have collection timestamps, but the selector has no maximum-age
   filter. Freshness is currently enforced by workflow review.
2. Premier Injuries still depends on browser connectivity; Betinf can use its
   public pages directly, and completeness still requires source review.
3. `main.py` can fall back to sample data, demo fixtures, or an unfitted default
   model. These paths are useful for development but must not authorize an
   official staking card.
4. Split half-win/half-loss payout accounting is not implemented. The
   Polymarket importer explicitly rejects quarter Asian handicaps, but does not
   apply the same explicit quarter-line check to totals; the final-card audit
   must exclude any such market.
5. Browser result buttons are non-authoritative local state.
6. The current automated tests cover important evidence, roster, news,
   Polymarket discovery, and learning safeguards, but they are not a complete
   live end-to-end source test.
7. Some context signals depend on proxies or manual context. Missing required
   evidence must block candidates; available weak evidence must not be
   overstated.
8. Backtest or research profitability does not establish current live edge.

## 15. Initial verification snapshot

This snapshot verifies the repository state used to write version 0.1. It is
not a reusable statement of current picks or performance.

- `python -m unittest discover -s tests -p 'test_*.py'`: **21 tests passed**.
- Targeted production Python compilation: **passed**.
- SQLite `PRAGMA quick_check`: **ok**.
- The repository already contained unrelated modified and untracked work; this
  initial spec did not reset, overwrite, or revert it.

## 16. Change discipline

Update this specification when a change alters any of the following:

- supported leagues, sources, markets, or settlement semantics;
- model or adjustment-layer behavior;
- evidence states or context gates;
- bankroll, staking, risk-band, or Parley policy;
- learned ranking or hard-loss rules;
- persistent schema or source-of-truth boundaries;
- weekly execution order, required validation, or reporting; or
- research-to-production promotion rules.

Pure refactors, formatting changes, and generated dashboard rebuilds do not
require a spec change unless they alter observable behavior or an acceptance
criterion.
