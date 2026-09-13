---
name: rollostake-operations
description: Run RolloStake results-and-learning reviews, the `/stake` full weekly workflow, or the pending-count `update` workflow while preserving each stage's scope.
metadata:
  internal: true
---

# RolloStake Operations

## Results and Learning Without Picks

Use this mode when Rollo asks to update, study, analyze, or understand the
completed week's results and excludes coming-week picks. It is a limited
operation, even when few picks remain pending.

1. Verify final scores from current external sources and settle only eligible
   completed singles and Parlays. Preserve future and still-live records.
2. Report Low Risk, High Risk, and Parley separately: wins, losses, pushes,
   hit rate, amount staked, P&L, ROI, live bank, and pending count.
3. Analyze meaningful splits by market, selection and line, odds bucket,
   predicted-probability/calibration bucket, quality, league, and active context
   layers. Explain the main wins and losses, including whether injury, team
   news, rotation, rest, fatigue, or European scheduling was material.
4. Study friend cards only as secondary aggregate structure evidence when they
   are available and in scope.
5. Record an evidence-backed decision to retain or change a rule. Do not force
   a model change from a small sample or claim performance improvement before
   later picks settle.

Stop after the review. Do not fetch the next slate, refresh odds, fit
predictions, preview or publish a card, or rebuild the dashboard unless Rollo
separately requests those stages.

## Weekly `/stake` Workflow

Use this overview when the user types `/stake` or asks for weekly predictions.
Read `WEEKLY_AGENT_HANDOFF.md` for the complete sequence and final validation.

1. Check git status.

```powershell
git status -sb
```

2. Verify completed scores against current external sources, update the result
   input as needed, then import. The final-window guard alone does not verify a score.

```powershell
python scripts\import_match_results.py match_results.csv
```

3. Study friend cards if available.

```powershell
python scripts\study_external_card.py friend_cards
```

4. Fetch upcoming fixtures for the five leagues through token-free HTTP.

```powershell
python scripts\fetch_weekly_fixtures.py --days 7
```

This uses ESPN scoreboard JSON and does not require `FOOTBALL_DATA_TOKEN`.
Note: `site.api.espn.com` edge-blocks this machine (HTTP 403), so the script
uses the identical `site.web.api.espn.com` mirror. Team-name aliases in
`utils/team_normalizer.py` must keep ESPN display names and Polymarket names
converging on the same canonical names used by historical data (e.g.
`TSG Hoffenheim` -> `Hoffenheim`, `AS Monaco` -> `Monaco`,
`Deportivo` -> `La Coruna`, `Stade Rennais` -> `Rennes`), otherwise odds
resolution silently skips matches.

Before continuing, identify every team in the upcoming domestic fixture pool
that played in the intervening UEFA Champions League, Europa League, or
Conference League round. Every relevant match must be final. Review the final
score, starting lineup, substitutions and player minutes, injuries,
suspensions and red cards, rotation, extra time, travel, rest, and material
performance evidence. Refresh those effects through the project's squad,
team-news, fatigue, and rotation inputs. A live, postponed, abandoned, or
unresolved relevant match blocks fitting, odds import, card preview, and
publication. Do not infer fitness or future strength from the score alone.

```powershell
python scripts\import_uefa_context.py --dry-run
python scripts\import_uefa_context.py
```

Continue to the live step only when the dry run reports zero unresolved
relevant matches. The import stores completed relevant UEFA rows and refreshes
the weekly European-schedule and rotation evidence used by the model.

5. Refresh current squads and positional depth for all five leagues.

```powershell
python scripts\fetch_squad_depth.py --dry-run
python scripts\fetch_squad_depth.py
```

The refresh is fail-closed: it validates every club roster before replacing
the current snapshot. Squad membership is context, not an automatic positive
adjustment. Layer 7 uses roster positions to classify injury/suspension impact.

Refresh team news before fitting, following the evidence and deduplication
rules in `WEEKLY_AGENT_HANDOFF.md`:

```powershell
python scrapers\browser_news_scraper.py --dry-run
python scrapers\browser_news_scraper.py
```

6. Fit/update predictions from the saved fixtures.

```powershell
python main.py --skip-scrape --no-fatigue --predictions-only
```

7. Dry-run Polymarket odds before writing.

```powershell
python scripts\scrape_polymarket_full.py --days 7 --dry-run
```

Only continue if unresolved matches are acceptable and no fake fixtures are created.

8. Import Polymarket odds.

```powershell
python scripts\scrape_polymarket_full.py --days 7
```

Never use `--create-missing` unless the user explicitly asks.

9. Draft and audit the card before publication, following the final-card gate
in `WEEKLY_AGENT_HANDOFF.md`. Changed inputs require a new preview.

```powershell
python scripts\rebuild_card.py --preview output\card-review.json
```

After Codex's evidence review and zero blockers:

```powershell
python scripts\rebuild_card.py --publish-reviewed output\card-review.json
```

10. Validate touched Python files.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -m py_compile analysis\adjustment_layers.py analysis\edge_calculator.py dashboard\generator.py scrapers\browser_news_scraper.py scripts\adjustment_layer_report.py scripts\fetch_weekly_fixtures.py scripts\fetch_squad_depth.py scripts\import_uefa_context.py scripts\rebuild_card.py scripts\import_match_results.py scripts\study_external_card.py scripts\scrape_polymarket_full.py scripts\import_historical_odds.py utils\match_resolver.py
```

## `update` Workflow

When the user says `update` without narrowing the scope, do this sequence. If
the user asks for results/learning and excludes picks, use the mode above and
do not enter this pending-count branch.

1. Check git status.

```powershell
git status -sb
```

2. Import/settle completed results only.

```powershell
python scripts\import_match_results.py match_results.csv
```

Verify new final scores against current external sources before adding them to
the import file. The importer must skip any match whose final-result window has
not passed in Macau time. Settled history must show match played/kickoff date,
never settled/import date.

3. Study friend cards from the repo folder.

```powershell
python scripts\study_external_card.py friend_cards
```

4. Count remaining pending picks.

```powershell
@'
import sqlite3
from config.paths import DB_PATH
conn = sqlite3.connect(DB_PATH)
count = conn.execute("SELECT COUNT(*) FROM picks WHERE status='pending'").fetchone()[0]
conn.close()
print(count)
'@ | python -
```

5. If pending picks are `2` or more, rebuild the card and dashboard.

   IMPORTANT — what "rebuild" actually does: `scripts\rebuild_card.py` re-renders the
   dashboard AND regenerates the official risk-band picks and parley slips.
   It preserves every pending pick until normal settlement. A refresh only adds
   picks on unused matches up to the band's total card ceiling. Existing Parley
   slips remain intact; new slips avoid existing and new card exposure.
   It does NOT fetch fixtures, odds, squads, or refit the model. Report this as
   "rebuilt card + dashboard (pick regeneration included), no fetch/refit".
   A literal dashboard-render-only request does not authorize this command.

```powershell
python scripts\rebuild_card.py --preview output\card-review.json
```

Audit the preview before `--publish-reviewed output\card-review.json`.
Odds/predictions older than 6 hours block publication. If refresh is outside
the requested update scope, report the blocker; do not relabel stale inputs.

6. If pending picks are fewer than `2`, fetch/find next-week fixtures and odds, then rebuild predictions.

```powershell
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

Codex audits the preview, then uses `--publish-reviewed output\card-review.json`.

Never use `--create-missing` unless the user explicitly asks. Do not clear past pending picks before settlement; `analysis/edge_calculator.py` should only replace pending picks for matches that have not kicked off yet.

7. Report High Risk, Low Risk, and Parley separately with record, P&L, ROI, bank, and remaining pending count.
