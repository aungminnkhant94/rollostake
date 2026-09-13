# RolloStake Weekly Agent Handoff

This document is the reusable operating brief for any AI model or agent running
Rollo's weekly football-prediction cycle.

This sequence applies to an authorized full weekly run. For `update`, limited
operations, reviews, and instruction-only edits, use the scope rules in
`AGENTS.md`; reading this document does not authorize executing its commands.
The Codex model preference and stable harness identity are also defined there.

## Copy-Paste Prompt

```text
Run the complete RolloStake weekly workflow in
C:\Users\aungm\Desktop\rollostake.

Read AGENTS.md and WEEKLY_AGENT_HANDOFF.md completely before acting. If the
RolloStake `stake` skill is available, use it. Do the work rather than only
describing a plan.

Settle only verified completed matches, learn separately from Low Risk, High
Risk, and Parley results, study any friend cards, refresh the coming seven-day
fixture slate, and identify relevant midweek UEFA matches for teams on that
slate. Do not fit or build the card until every relevant Champions League,
Europa League, and Conference League match is final. Review those matches for
scores, player minutes, rotation, injuries, suspensions, red cards, extra time,
travel, rest, and material performance evidence. Then refresh current squads,
positional depth, and team news, fit the model, dry-run and import Polymarket
odds, rebuild the official card, and rebuild the dashboard.

Do not accept the first generated card automatically. Audit every selection and
remove filler. Preserve the current stricter Low Risk rules: no SKIP picks,
minimum 55% model probability, minimum 10% edge, maximum ten picks, complete
required context evidence, and a veto when multiple football-context signals
contradict a goal-market pick. Keep the learned team-total Under 1.5 trap
separate from match-total Under 1.5. Never use unsupported quarter Asian
handicaps. Do not generate a risk-band pick if its live bank is below the flat
stake.

Rollo's EPL knowledge is useful evidence. When he identifies attacking quality,
lineup strength, tactical changes, or something he watched, compare it with the
model and sourced team news. Use a strong contradiction as a review flag or
veto; do not invent a numerical adjustment or blindly agree.

Prefer fewer well-supported picks over filling a quota. Verify that every newly
generated pick is future-dated, unique by match, backed by fresh odds, supported by the
required adjustment layers, and consistent with current squads and news. Run
the full tests, Python compilation, database integrity check, and browser QA of
the dashboard before reporting completion.

Report the final singles and parlays separately, with Macau kickoff time, exact
market meaning, odds, model probability, edge, and stake. Report Low Risk, High
Risk, and Parley records, P&L, ROI, banks, pending counts, blocked candidates,
what the system learned, what changed from the previous week, and whether the
actual hit rate improved after settlement. Never promise a win. Do not push Git
changes unless Rollo asks.
```

## Weekly Timing Gate

The weekly cycle has three distinct stages:

1. **Results and learning:** verify completed scores, settle eligible singles
   and Parlays, measure Low Risk, High Risk, and Parley separately, explain the
   main winning and losing shapes, check calibration and context evidence, and
   record an evidence-backed keep/change decision. This stage can run alone and
   does not authorize any new picks.
2. **European observation:** after the coming domestic fixture slate is known,
   identify its teams that played in the intervening UEFA Champions League,
   Europa League, or Conference League round. Wait until every relevant match
   is final, then review the score, lineup, substitutions and minutes, injuries,
   suspensions and red cards, rotation, extra time, travel, rest, and material
   performance evidence. A score alone is not enough to infer team condition.
3. **Prediction and publication:** run only when Rollo requests the coming-week
   picks. Refresh the evidence affected by stage 2 before fitting or pricing.

If any relevant European match is live, postponed, abandoned, or unresolved,
report it and stop before model fitting, odds import, card preview, or
publication. "Relevant" means a European match involving a team in the coming
domestic fixture pool; it does not require reviewing unrelated UEFA matches.

## Required Execution Order

Run from `C:\Users\aungm\Desktop\rollostake`.

1. Inspect the worktree and preserve existing changes.

   ```powershell
   git status -sb
   ```

2. Import verified results only. Future and still-live matches must remain
   pending. After settlement, calculate separate Low Risk, High Risk, and
   Parley record, P&L, ROI, bank, and pending counts. Analyze performance by
   market, selection and line, odds and predicted-probability bucket, quality,
   league, and material context layers. State whether the evidence supports a
   rule change or retaining the current rules; do not force a change from a
   small sample.

   ```powershell
   python scripts\import_match_results.py match_results.csv
   ```

3. Study the saved friend cards as aggregate structure evidence, never as picks
   to copy.

   ```powershell
   python scripts\study_external_card.py friend_cards
   ```

4. Fetch the coming seven-day fixtures for all five leagues.

   ```powershell
   python scripts\fetch_weekly_fixtures.py --days 7
   ```

5. Apply the European observation gate. For every coming-fixture team that
   played in the relevant midweek UEFA round, confirm the match is final and
   review its result and player/team effects listed above. Do not continue if
   any relevant match is unresolved.

   ```powershell
   python scripts\import_uefa_context.py --dry-run
   python scripts\import_uefa_context.py
   ```

   The live step stores only completed relevant UEFA matches, writes the
   current weekly observation artifact, and refreshes European-schedule and
   rotation review evidence for the coming domestic teams.

6. Refresh post-transfer-window squads and positional depth. The dry run must
   validate all clubs before the live snapshot is replaced.

   ```powershell
   python scripts\fetch_squad_depth.py --dry-run
   python scripts\fetch_squad_depth.py
   ```

7. Refresh team news fail-closed. Never append a second copy of the same injury
   feed.

   ```powershell
   python scrapers\browser_news_scraper.py --dry-run
   python scrapers\browser_news_scraper.py
   ```

8. Fit the model and save the 14-layer context audit.

   ```powershell
   python main.py --skip-scrape --no-fatigue --predictions-only
   ```

9. Fetch Polymarket odds safely. Continue to the live import only when the dry
   run has acceptable unresolved coverage, zero bad rows, and zero fake fixture
   creation.

   ```powershell
   python scripts\scrape_polymarket_full.py --days 7 --dry-run
   python scripts\scrape_polymarket_full.py --days 7
   ```

   Never use `--create-missing` unless Rollo explicitly requests it.

10. Create a draft and inspect its singles, new Parley slips, blockers, and
   overlap exclusions before publishing. Preview does not write the database
   or dashboard. If the draft contains filler, weak probabilities,
   `SKIP` quality, missing evidence, repeated loss traps, or football-context
   contradictions, identify the cause. Correct and rebuild when that work is
   authorized; otherwise report the exact blocker. Do not loosen gates or tune
   parameters merely to produce picks.

   ```powershell
   python scripts\rebuild_card.py --preview output\card-review.json
   ```

   Codex audits the draft against the final-card rules below. Publish only the
   exact reviewed artifact with zero blockers. If inputs change, repeat the
   preview and review; do not edit the JSON to bypass a blocker.

   ```powershell
   python scripts\rebuild_card.py --publish-reviewed output\card-review.json
   ```

   Publication requires exact-market odds and predictions no older than 6
   hours. Every existing pending pick remains active and visible until normal
   settlement. A refresh only adds picks on unused matches, up to the band's
   total card ceiling. Existing Parley slips remain intact. New singles avoid
   pending single and Parley matches, and new slips avoid all pending exposure,
   proposed singles, and each other. Do not present
   retained older pending records as freshly reviewed recommendations.

11. Review model evidence for the final pending picks.

    ```powershell
    python scripts\adjustment_layer_report.py
    python scripts\news_impact_report.py
    ```

12. Validate before delivery.

    ```powershell
    $env:PYTHONDONTWRITEBYTECODE='1'
    python -m unittest discover -s tests -p 'test_*.py'
    python -m py_compile analysis\adjustment_layers.py analysis\edge_calculator.py dashboard\generator.py scripts\adjustment_layer_report.py scripts\fetch_squad_depth.py scripts\rebuild_card.py scripts\import_match_results.py scripts\study_external_card.py scripts\scrape_polymarket_full.py scripts\import_historical_odds.py utils\match_resolver.py scrapers\browser_news_scraper.py
    git diff --check
    ```

    Also run SQLite `PRAGMA quick_check` against `data\rollo_stake.db`, then open
    `dashboard\index.html` through temporary localhost and verify desktop and
    mobile rendering. The dashboard's uncovered-fixture table uses 10 rows per
    page with visible page controls when uncovered fixtures exist.

## Final Card Audit

Every newly generated official single must pass all of these checks. Preserve
older pending picks whose matches have already started so they can settle;
do not delete them to make the new-card audit pass.

- Kickoff is still in the future and inside the requested window.
- The match exists in the fetched fixture slate.
- Polymarket odds are fresh and resolved to that exact existing match.
- Market is one of `1X2`, `OU`, `BTTS`, `TT`, or supported `AH`.
- No quarter Asian handicap is used.
- Only one official pick is selected per match.
- The risk band can afford its flat stake.
- Low Risk has model probability at least 55%, edge at least 10%, and quality
  `KEEP` or better.
- Required context layers have usable evidence. Goal markets require rolling
  form, Elo/strength of schedule, finishing evidence, injuries, and rest days.
- Learned hard-loss traps do not match.
- A goal pick with multiple stronger contradictory context signals is rejected.
- Team-total and match-total markets are never confused.

## Improvement Contract

Improvement is not "the model changed" and is not "more picks." Each weekly
cycle must show:

1. The previous card's settled record, P&L, ROI, and losing shapes.
2. Calibration and performance splits by market, selection/line, odds and
   probability bucket, quality, league, and material context where the sample
   supports a conclusion.
3. The evidence-supported learning or selection change, or the reason no change
   is justified. A weekly run does not require parameter or code changes.
4. Which relevant midweek European matches were reviewed and how their lineups,
   minutes, injuries, discipline, rotation, extra time, travel, and rest affect
   the coming fixtures.
5. The new card's pick count and quality distribution.
6. The safeguards that removed candidates from the card.
7. After settlement, whether hit rate, P&L, and ROI actually improved.

Process improvement may be reported immediately. Performance improvement is
only proven after the matches settle.

## Current Reference Snapshot

This is a dated reference from the run completed on September 3, 2026. It must
be refreshed rather than reused as current truth.

- Latest settled weekly comparison baseline: 6W-5L.
- Low Risk all-time: 29W-20L-1P, +$103.741, +20.7% ROI, $203.741 bank.
- High Risk all-time: 4W-21L, -$96, -38.4% ROI, $4 bank; paused below stake.
- Parley all-time: 0W-4L, -$20, -100% ROI, $80 bank.
- The updated September 3 card contained ten Low Risk `KEEP` singles and no High Risk
  picks.
- The clean refresh covered 49 fixtures, 1,813 supported Polymarket odds rows,
  96 club squads with 2,842 players, and 355 deduplicated team-news rows.
- The run passed 21 tests, compilation, database integrity checks, automated
  card auditing, and desktop/mobile dashboard QA.

The next agent must query the live database and current sources before quoting
any of these numbers.
