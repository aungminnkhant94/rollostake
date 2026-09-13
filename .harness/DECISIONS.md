# Decisions

## 2026-09-08 — Separate weekly learning and European observation gate

- The completed-card review is a separate stage: verify and settle eligible
  results, report Low Risk, High Risk, and Parley separately, analyze meaningful
  performance and calibration segments, and record an evidence-backed
  keep/change decision. It does not generate coming-week picks.
- Before a requested coming-week card is fitted or priced, identify teams in
  the coming domestic fixture pool that played in the intervening Champions
  League, Europa League, or Conference League round. Wait until every relevant
  match is final.
- Review scores, lineups, substitutions and minutes, injuries, suspensions and
  red cards, rotation, extra time, travel, rest, and material performance
  evidence. Refresh affected context inputs before model fitting.
- A live, postponed, abandoned, or unresolved relevant European match blocks
  fitting, odds import, card preview, and publication. Unrelated UEFA matches do
  not need review for the card.

## 2026-09-05 — Authorized card remediation and review gate

Rollo approved implementation after the read-only card audit. This supersedes
the earlier instruction-only restriction for the scoped remediation: correct
BTTS probability, reconcile injury identities and missing recent history,
refresh evidence, recalculate, and audit before publication. Kimi collects and
computes within bounded assignments; Codex owns evidence review and publication.
Neither model independently invents picks outside the implemented algorithm.

`main.py` now defaults to predictions only. `rebuild_card.py --preview PATH`
writes a draft without touching the card; `--publish-reviewed PATH` checks exact
inputs/selections and freshness before saving. Replaced future recommendations
remain as `superseded`; settlement excludes them. Started/undated pending picks
and existing Parley slips remain intact. New Parley slips do not share matches
with existing exposure or new singles/slips. Retained older pending picks are
historical obligations, not a fresh endorsement.

Record durable project/architectural decisions here. Do not use this file as a chat transcript.

## 2026-09-04 — Rollo is never the inter-agent relay

- Rollo initiates a weekly run once; he is not responsible for copying prompts,
  command output, approvals, or status between Codex and Kimi K3.
- Codex decomposes the run and adds bounded Kimi work through
  `rollo_harness.cli task add`.
- Kimi K3 discovers ready work through `rollo_harness.cli context` or
  `rollo_harness.cli task list`, then records `task start`, `task done`, and a
  final `handoff` with evidence and next action.
- Codex reads Kimi's task results and handoff directly from `.harness`, performs
  the quality gate, and adds the next task without involving Rollo.
- Long-running mechanical stages belong to Kimi; reasoning, learning changes,
  official-card approval, and final reporting belong to Codex.
- Neither agent may ask Rollo to relay information that is available in the
  harness, working tree, logs, database, or Git history.

## 2026-09-05 — Astra High and instruction scope

- Rollo selected GPT-6 Astra with High reasoning for Codex work on RolloStake.
  Keep `codex-sol` as the existing harness identity; it is not the model setting.
- The model change does not replace the September 4 Codex/Kimi coordination
  agreement. Apply that agreement to authorized weekly work, not every review
  or instruction edit, and serialize writers to the live database and card.
- `AGENTS.md` selects the task scope; `WEEKLY_AGENT_HANDOFF.md` owns the full
  weekly sequence; the `stake` skill routes to those documents. Explicit user
  restrictions override workflow defaults.
- The September 5 instruction audit authorizes guidance and handoff changes
  only. It does not authorize code, settings, betting-data, or dashboard changes.
- Corrected the documented rebuild behavior: future pending picks can be
  replaced, settled and known already-started picks are preserved, and unknown
  kickoffs remain a documented implementation gap. No implementation was changed.
