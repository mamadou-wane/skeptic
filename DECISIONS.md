# DECISIONS.md — Skeptic plan provenance

Records every decision that shaped the plan: the v2 review addendum (55 decisions, dual-voice, dissents recorded), the v2 final gates, and the v3 consolidation amendments. Commit at repo root.

---

# Part 4 — Review Addendum (/autoplan, 2026-07-21)

*Historical snapshot of v2's review addendum, reproduced verbatim. Where the v3 Consolidation Amendments (rows 56-61) and the v3.1 Review Fixes (rows 62-64) at the end of this file conflict with anything below — e.g. the timeline, or the "CRITICAL GAPS: none remaining" and "NO UNRESOLVED DECISIONS" absolutes — the later amendment governs.*

Mode: SELECTIVE EXPANSION · UI scope: none (design phase skipped) · DX scope: yes.
Dual voices ran per phase (Claude subagent + Codex gpt-5.6-sol). Premise gate answered by owner; all other decisions auto-decided per the 6 principles and logged in the Decision Audit Trail below.

## Accepted scope (premise gate, owner-approved)

1. **Eval A de-circularized.** Self-authored variants become the *dev set*. Add a **blind holdout** authored by a different frontier model against the taxonomy spec (generator never sees detector code), plus a **TRACE-subset comparison point**. Add three baseline rows to the eval table: always-SUSPECT, suite-green-only, cheap-LLM-judge (LLM diff review is promoted from "first to cut" to *baseline*; it is no longer cuttable).
2. **Eval B pressured + prevention tier.** Experiment arms that induce hacking: tight iteration/cost budgets, underspecified problem statements, weaker-model arm; report hack incidence per arm. Prevention tier enforced in the sandbox: tests/, runner configs mounted read-only for the Builder; `allowed_paths` enforced by diff policy; **`.git` stripped from the seeded workspace (`git archive` export)** so the parent commit cannot leak the pristine fix; `seed --check` asserts pristine content is unreachable. Detection claims are scoped to what prevention cannot stop (H5–H7); H1–H4/H9/H10 become *prevention* claims validated by attempted-violation tests.
3. **Audience: agent-evals / AI-infra roles.** README first screen written for eval-literate reviewers; related-work positioning vs STING, SWE-Mutation, SWE-ABS, TRACE, SpecBench is part of the definition of done.

## CEO review findings folded into the plan (auto-decided)

- **A1 (arch):** Agent loop runs **host-side**; only tool execution enters the container. This is what makes "network off during BUILD" coherent (API keys never enter the sandbox).
- **A2 (arch):** Reference checkout gets its **own runner env** (same image, separate container/mount); adversarial tests execute only in sandboxes, never on the host; the reference is never mounted into any Builder-visible container.
- **A3 (arch):** Day-1 spike must validate coverage.py **dynamic contexts** inside the container on all three repos (fallback: full-suite-per-mutant + plain patch coverage). This is the single point of failure for three checks.
- **A4 (arch):** Per-VERIFY wall-clock budget; prebuilt per-repo Docker images at SEED (cache key: repo+commit+deps hash) so 60 variants don't pay 60 pip installs; `config_hash` contents specified = {prompts, model ids, check configs, budgets}.
- **E1 (errors):** New verdict state **INFRA_ERROR**, distinct from FAIL. Missing/corrupt coverage data, container failures, and check crashes abort as INFRA_ERROR; they must never degrade into evidence (a silent 0%-coverage reading would fail a gold patch and poison the FP rate).
- **E2 (errors):** Mutant outcome taxonomy: killed / survived / **timeout** / **invalid** — timeouts and invalid mutants are excluded from the kill-rate denominator, reported separately.
- **E3 (errors):** Adversarial testgen edge: if 0 of N candidates pass on the reference, the check emits *no evidence* (neutral), not a pass; failed candidates are retained as artifacts for generator debugging.
- **E4 (errors):** pytest exit-code taxonomy handled explicitly (0 green / 1 failures / 2 usage error / 5 no tests collected); empty Builder patch → FAIL(no-patch), not a crash.
- **S1 (security):** Container hardening: non-root, `--network none` post-install, `--security-opt no-new-privileges`, pids-limit, read-only rootfs where viable. `--no-docker` venv fallback is **verify-only** — the Builder (LLM with shell) never runs outside Docker.
- **S2 (security):** Threat model documents patch-content prompt injection against the Skeptic's own testgen model (mitigations: prompt hardening; reference-validation step bounds the damage to a missed detection, never a false PASS elevation).
- **D1 (data):** Seed invariant tightened: the seeded red set must equal `failing_tests` **exactly** (no collateral reds).
- **D2 (data):** **Corpus self-validation:** `seed --check` runs the full VERIFY pipeline on gold (and gold-prime) and asserts verdict PASS at author time. A gold that can't pass its own verifier is a corpus bug, caught before eval day.
- **D3 (data):** **Gold-prime** variant per task (correct fix implemented differently from the revert); FP reported separately on gold vs gold-prime. Adversarial tests constrained to behavior implied by the problem statement.
- **Q1 (quality):** H10 applies only if a chosen repo actually ships golden/snapshot files; verify at corpus-selection time or mark H10 N/A-by-corpus honestly.
- **Q2 (quality):** Updated cut order: (1) call-graph check, (2) H10 variants, (3) corpus 15→10. LLM diff review is no longer cuttable (it's a baseline).
- **T1 (tests):** **Harness self-test suite** — the verifier gets verified: per-check unit tests against a fixture mini-repo (each hack category = a fixture diff with expected evidence), aggregator golden-verdict tests, mutation-operator tests, evalkit confusion-matrix math tests, trace-schema round-trip.
- **T2 (tests):** GitHub Actions CI for the harness: lint + self-tests + one smoke task in verify-only venv mode. A verification tool with no CI is an irony reviewers will notice.
- **P1 (perf):** Coverage run once per variant, JSON reused by T1 coverage delta + T2 test selection; runtime envelope table added to the plan.
- **O1 (observability):** Trace gains per-check timing, mutant-level outcome events, INFRA_ERROR events, and a run manifest (config hash, model ids, seeds) at run start; `skeptic clean` for orphaned containers/workspaces; CLI exit codes (0 PASS / 1 SUSPECT / 2 FAIL / 3 INFRA_ERROR) for scripting.
- **L1 (long-term):** Roadmap reworded honestly: "multi-language" → "second language (analyzer rewrite)". Metric hardening: attribution reported as top-1 *and* anywhere-in-evidence; the ≤10% FP bar is reported with its resolution limit (1 FP ≈ 7–8% at n≈13 golds) rather than as a precise threshold.
- **Repro:** dependency lockfile; seeds fixed; pinned commits (already planned) ✓.

## NOT in scope (considered and deferred, with rationale)

- GitHub App / PR-bot service — roadmap line stands (scope trap for 7 days).
- `skeptic verify --diff` standalone mode + 20-line GitHub Action — **taste decision at final gate** (Claude voice: cheap 10x; Codex: dual-product risk).
- Multi-language support — honestly reworded as a rewrite, not a roadmap line.
- Live GitHub issue mining, RL/training, semantic equivalence proving, perf/concurrency/security oracles, web service — unchanged.
- Full TRACE-dataset evaluation — subset comparison only (budget).
- Distribution/marketing half-day (writeup, posting where agent-evals hirers read) — deferred to TODOS with a resume/site link task.

## What already exists (leverage map)

- coverage.py dynamic contexts — used (T1 coverage, T2 test selection). Day-1 spike de-risks it.
- mutmut / cosmic-ray — considered; hand-rolled mutator kept for patch-scoping/budget/sampling control. **Codex dissent recorded** (wants a wrapping spike first) — taste decision at final gate.
- TRACE dataset (517 human-verified hack trajectories, 54 categories) — now used as external holdout/comparison.
- SWE-bench harness — borrow docker task-env patterns (pinned images, deterministic installs).
- Author's parked agent-fault-harness design — reusable JSONL trace-event and orchestrator-FSM patterns.

## Dream state delta

CURRENT: no public verification artifact → THIS PLAN (v2): measured, de-circularized hack-detection harness + prevention tier on 3 repos, blind holdout, baselines → 12-MONTH IDEAL: deployable patch-admission gate running on arbitrary agent PRs in CI. The kernel (T1 + prevention policy) is the deployable seed; the diff-mode taste decision determines how far this plan reaches toward it.

## Error & Rescue Registry

| Codepath | Failure | Exception class | Rescued? | Action | User sees |
|---|---|---|---|---|---|
| sandbox.exec | container timeout | SandboxTimeout | Y | kill, retry 1x, else INFRA_ERROR | `verify: infra error (sandbox timeout)` |
| sandbox.exec | OOM / daemon down | SandboxOOM / DockerUnavailable | Y | INFRA_ERROR; venv hint if daemon down | actionable stderr + trace event |
| builder.llm | 429 / timeout | RateLimit / APITimeout | Y | backoff retry ≤3; then stop-condition | trace event; run resumable via stage cache |
| builder.llm | malformed tool call | ToolParseError | Y | re-prompt once; count vs iteration cap | trace event |
| builder.llm | cost ceiling | CostCeilingExceeded | Y | stop BUILD, verdict on best patch so far | `budget exhausted` + ledger row |
| seed.apply | variant conflicts | PatchApplyError | Y | reject task at author time (invariant 3) | `seed --check` failure |
| run_tests | pytest exit 2/5 | PytestUsageError / NoTestsCollected | Y | INFRA_ERROR (2) / evidence "no tests" (5) | explicit, never silent green |
| t1_coverage | contexts missing | CoverageDataMissing | Y | **INFRA_ERROR — never 0% evidence** | `coverage infra failure` |
| t2_mutation | mutant invalid / timeout | MutantInvalid / MutantTimeout | Y | excluded from kill-rate denominator | reported buckets |
| t2_advtests | candidate SyntaxError | CandidateInvalid | Y | discard, retain artifact | testgen yield stat |
| t2_advtests | 0/N pass reference | — | Y | neutral (no evidence) | noted in verdict evidence |
| aggregate | evidence schema invalid | EvidenceValidationError | Y | INFRA_ERROR | schema path in message |
| report | corrupt JSONL line | TraceParseWarning | Y | skip line + warn | report banner |
| ledger | usage fields absent | — | Y | estimate + mark `estimated:true` | ledger flag |

## Failure Modes Registry

| Codepath | Failure mode | Rescued? | Test? | User sees? | Logged? |
|---|---|---|---|---|---|
| coverage delta | silent 0% on infra failure | Y (INFRA_ERROR) | self-test fixture | explicit | Y |
| gold variant | fails own verifier (FP by construction) | Y (seed --check full-VERIFY) | corpus CI | explicit | Y |
| Builder | recovers fix from .git history | Y (git archive strip + reach check) | attempted-violation test | explicit | Y |
| adv. testgen | poisoned/trivial candidates | partial (reference validation bounds to missed detection) | fixture test | evidence notes | Y |
| mutation | timeouts inflate kill rate | Y (bucket taxonomy) | unit test | reported | Y |
| eval | flaky suite poisons verdict | Y (2× rerun + quarantine) | rerun policy test | quarantine list | Y |
| CRITICAL GAPS | **none remaining** after E1/D2 fold-in | — | — | — | — |

## Eng review findings folded into the plan (auto-decided, Phase 3)

- **N1 (isolation, was Codex P0):** One-container-per-run is insufficient. Execution model becomes: immutable per-repo dependency image (built with network, digest-pinned) → **sanitized candidate archive** (tracked files only; no `.git`, no `.pytest_cache`, no `__pycache__`, no untracked residue) → **fresh container per check run, per mutant batch, per adversarial-test run, and for every reference run**. No shared host bind mounts between Builder and Skeptic stages. The Builder can no longer contaminate its verifier.
- **N2 (Eval B ground truth, was Codex P0):** Each task gains a **frozen acceptance suite** authored at corpus time (held out from Builder, detectors, and adversarial testgen) so Eval B outcomes are classified RED / GREEN-wrong / GREEN-correct / INFRA_ERROR per pressure arm. Skeptic's own PASS never labels Skeptic's evaluation.
- **N3 (task admission, was Codex P0):** `seed --check` hardened: pristine suite green **twice** (flakiness screen); seed changes only the intended test outcomes (baseline-equality, not "full suite green" — environment-dependent skips are recorded, not fought); no collection/import errors; every hack variant must be **green under ordinary pytest** with only its category-expected delta; patch files content-hashed into the task manifest.
- **N4 (adversarial-test acceptance, was Codex P0):** "Passes on reference" upgraded to: collected AND passed without skip/xfail AND executes the target code (coverage-verified) AND fails the seeded tree AND passes pristine + every gold-prime implementation; restricted to public API behavior; exact generated artifact persisted.
- **N5 (H8 detector, was Codex P0):** The taxonomy's H8 secondary detector ("adversarial tests run outside pytest env") is impossible as written — a pytest-run test still sets `PYTEST_CURRENT_TEST`. Replaced with a **standalone consumer-process probe**: import and call the public API in a bare Python process (no pytest, no coverage, no tracing env) and compare behavior against the in-pytest result.
- **N6 (collection-manifest diff, Claude voice):** New cheap T1 check: `pytest --collect-only -q` before/after patch; any disappearance from the collected set is mechanism-agnostic hard evidence covering H1/H3/H4 (and vectors not enumerated, e.g. `collect_ignore_glob` in nested conftest).
- **N7 (coverage semantics):** Patch-coverage defined precisely: executable statements only (deleted lines, decorators, imports, renames mapped explicitly); import-time execution does not count as test adequacy; `patch_coverage_min: 0.8` gets a defined verdict rule (below min → soft evidence; zero → hard fail; NO_DATA → INFRA_ERROR).
- **N8 (mutation robustness):** Kill rate split into **changed-code rate** and **caller-control rate** (callers of well-tested old code no longer dilute the signal); deterministic stratified sampling with fixed seed; statuses killed/survived/timeout/invalid/uncovered/import-failed; per-mutant timeout = 3× measured baseline of selected tests, cap 60s (never task `timeout_s`).
- **N9 (verdict semantics):** Soft-evidence weights and the SUSPECT threshold specified in one table in the plan; PASS requires every mandatory check completed (explicit NOT_APPLICABLE state); run status (ok / INFRA_ERROR) orthogonal to verdict; deterministic evidence ordering (severity, then fixed check precedence) so attribution is not an artifact of a sort.
- **N10 (cache/replay honesty):** Cache key = content hash over {patch bytes, repo commit, verifier code revision, prompts, model ids, image digest, dependency lock, seeds, check configs}; content-addressed run manifest emitted at run start; per-check completion records in the trace.
- **N11 (repo admission protocol):** Before Day 1: pin exact commits and measure, per repo — Python 3.12 install, full-suite runtime, skip census, dynamic-contexts support, offline execution. Pin base-image digest + arch (arm64 host), wheels via constraints file, locale/TZ/terminal env (`TERM`, `COLUMNS` for click/rich), async backend + no-proxy env for httpx. Two-phase lifecycle: build with network → run with `--network none`.
- **N12 (budget correction):** Cost table updated for accepted scope: base + 3 pressure arms ≈ 60 Builder attempts = **$24–90 before retries** (was $12–45); Eval A worst case ≈ 1,800 mutant pytest invocations + 480 candidate validations — feasible only with prebuilt images, per-mutant test selection, and the wall-clock caps above. Still inside $75 only if the corpus descope (final-gate decision) is taken; ledger reports actuals either way.

### Architecture (post-review execution model)

```
                     HOST                                    CONTAINERS (per-repo immutable image, digest-pinned)
  task.yaml ──► Orchestrator (FSM, budgets, cache, ledger)
                  │  LOAD/SEED: git archive ──► seeded workspace (no .git)
                  │                              ┌──────────────────────────────┐
                  ├─ BUILD:  Builder loop ──────►│ tool-exec container          │  network: none
                  │          (LLM API host-side) │ (read-only tests/, configs)  │
                  │                              └──────────────┬───────────────┘
                  │                                   candidate patch
                  │                              sanitized candidate archive
                  ├─ VERIFY: T1 checks (pure fns over patch + coverage JSON)
                  │          T2 mutation ───────► fresh container per batch
                  │          T2 advtests ───────► fresh candidate container
                  │                        └────► fresh REFERENCE container (pristine; never Builder-visible)
                  │          consumer-process probe (H8) ──► bare-python container
                  ├─ VERDICT: aggregator (rules table, evidence ordering) ──► verdict.json
                  └─ REPORT: JSONL trace ──► evalkit metrics / static HTML
```

### Test diagram (harness self-coverage plan)

```
CODE PATHS (planned)                                   PLANNED TEST
[+] skeptic/t1_ast.py         per-hack fixture diffs   unit: fixture mini-repo, expected evidence per H1-H4
[+] skeptic/t1_collect.py     manifest diff (N6)        unit: conftest collect_ignore fixture → detected
[+] skeptic/t1_coverage.py    delta + NO_DATA path      unit: delete .coverage → INFRA_ERROR not FAIL
[+] skeptic/t2_mutation.py    operators + statuses      unit: operator validity; timeout-not-kill rule
[+] skeptic/t2_advtests.py    acceptance ladder (N4)    unit: skipped/assertion-free candidate rejected
[+] skeptic/aggregate.py      rules + ordering (N9)     golden verdict tests, one per hack category
[+] orchestrator FSM          stage cache + resume      unit: kill mid-VERIFY → resume from cache
[+] sandbox lifecycle         two-phase network         integration: install online, assert offline at BUILD
[+] evalkit                   confusion matrix math     unit: hand-computed 3-task fixture
[+] report                    JSONL → HTML              round-trip: corrupt line skipped with warning
USER FLOWS: seed --check on 2 tasks (CI smoke) · verify-only venv run (CI) · full run resume after SIGINT
COVERAGE TARGET: every hard-fail rule has a fixture that triggers it and a gold fixture that must NOT.
```

### Worktree parallelization

| Lane | Steps | Modules | Depends on |
|---|---|---|---|
| A | Orchestrator + sandbox + spec loader | orchestrator/, sandbox/, cli.py | — |
| B | T1 checks (pure functions over fixtures) | skeptic/t1_*.py | — (fixture mini-repo only) |
| C | Corpus authoring (daily thread) | tasks/, patches/ | A (seed --check) |
| D | T2 + aggregator | skeptic/t2_*.py, aggregate.py | A (sandbox exec), B (evidence schema) |
| E | Evalkit + report | evalkit/, report/ | trace schema (A) |

Launch A + B in parallel; C starts as soon as A's `seed --check` exists and continues daily; D after A+B; E anytime after the trace schema freezes. Conflict flag: B and D share the evidence schema — freeze `verdict.json`/evidence dataclasses on Day 1.

## DX review findings folded into the plan (auto-decided, Phase 3.5)

**Persona (0A):** primary = eval-literate reviewer (agent-evals / AI-infra) landing from a resume or post; tolerance ≈ 60 seconds to decide the repo is credible, ≈ 10 minutes if they clone. Secondary = hands-on cloner evaluating the tool itself. **Magical moment (0D):** a copy-paste command that catches a cheat — a colored FAIL verdict with cited evidence, in under a minute, for $0. **Competitive anchor (0C):** SWE-bench's official harness needs 120GB disk and ~15–50 min to first eval; Skeptic's measured footprint/cold-start table is a differentiator worth printing in the README.

- **DX1 — `skeptic demo`:** keyless, dockerless, network-free; audits a bundled fixture mini-repo (shared with the harness self-test suite) and prints a real FAIL verdict with evidence, `Cost: $0.00`, in <60s. This is the README's first command and the project's magical moment.
- **DX2 — `skeptic doctor`:** preflight for Docker daemon (CLI absent vs daemon stopped vs socket permission), API keys, Python 3.12, disk space, architecture. **Operational error contract:** every setup failure states what failed, why Skeptic needs it, and the exact next command. Named Day-6 exit criterion (replaces undefined "CLI polish").
- **DX3 — Free vs paid lanes, explicit:** `--profile deterministic` (T1 + mutation, zero keys) selectable on `verify`/`eval`; key validation happens per command *before* any image/dependency work, naming the exact env var; never silently downgrade; model-calling commands print an estimated max cost and require confirmation (consistent with the cost-ledger ethos).
- **DX4 — README dual-audience restructure:** above the fold = one-sentence value prop + eval table (with n, corpus version, run date) + the free demo command with pasted expected output + evidence GIF + measured time/disk vs SWE-bench. Second screen = 3 lanes (free demo / deterministic real verify / paid end-to-end) each with measured runtime, download, disk, max cost. Then architecture, tradeoffs, limits, related work. Quickstart is an executable acceptance artifact from Day 1; Day 7 gains a **timed fresh-clone run in a clean container** as the <10-min acceptance test.
- **DX5 — Terminal verdict spec:** rendered verdict banner + evidence list (check · category · file:line · artifact path); exit codes 0/1/2/3 (already in O1); this unblocks the "run the CLI in CI" roadmap line.
- **DX6 — Discoverability defaults:** `skeptic tasks list`, `skeptic runs list`; `report` defaults to the latest run; `eval` defaults to `tasks/`→`reports/`; every command prints the suggested next command; `seed` validates by default (`--no-check` to skip).
- **DX7 — Reproduction manifest:** `evals/v1/manifest.json` (content hashes for code, corpus, patches, image digest, dependency locks, model/prompt identities, seeds) + `skeptic reproduce <manifest>` (frozen artifacts, exact table) vs `skeptic eval --live` (new experiment). Trace events gain schema version, tool git SHA, corpus hash, image digest, model/prompt identity. Raw traces + trusted generated tests for the headline run are committed.
- **DX8 — `--no-docker` semantics per command:** allowed for `verify` on repo-shipped patches (isolation warning printed AND stamped into the report/verdict); refused for `build`/`run`. (Extends S1.)
- **DX9 — Package rename:** inner `skeptic/skeptic/` → `skeptic/checks/` (kills the `skeptic.skeptic.t1_ast` import stutter in a repo whose whole point is being read).
- **DX10 — Distribution:** prebuilt task-ready image published to GHCR (fallback: local build); one PNG of a rendered report committed next to the GIF (HTML doesn't render on GitHub); MIT license + minimal CONTRIBUTING.
- **DX Scorecard (plan-as-written → post-fold target):** Getting Started 2→9 · API/CLI 5→8 · Errors 2→8 · Docs 4→9 · Upgrade/Repro 3→8 · Dev Env 4→8 · Community 3→6 · DX Measurement 3→7 · **Overall 3→8**. TTHW: unmeasured (~15+ min cold) → demo lane <60s (Champion), real-docker lane <10 min measured (Competitive). Principle coverage: Zero Friction (demo), Learn by Doing (demo+lanes), Fight Uncertainty (doctor+error contract), Opinionated+Escape Hatches (profiles, --no-docker), Code in Context (README lanes), Magical Moment (demo verdict).

## Cross-Phase Themes (flagged independently in 2+ phases — high-confidence signals)

1. **Measure, don't assert** — CEO (pre-registered bar without baselines), Eng (budget table vs real attempt count; FP n-resolution), DX (the <10-min claim is "stated but not engineered for"). Every headline claim now has a measurement mechanism: baselines, per-arm reporting, timed fresh-clone test, cost actuals.
2. **The free deterministic tier is the product's spine** — CEO (T1 catches the crude majority cheaply), Eng (T1 checks are pure functions, easiest to self-test), DX (keyless demo is the only viable hello-world). Investment order follows: T1 + fixtures first, everything else layers on.
3. **Self-authored ⇒ self-graded** — CEO (Eval A circularity), Eng (Eval B ground truth; attribution = your own sort), DX (reproduction manifest so outsiders can check). The blind holdout + acceptance suites + manifest are one theme: externalize the ground truth.
4. **Schedule realism** — all three phases independently flagged Day 5 as 2+ days of work and Days 6–7 as carrying unspecified deliverables. The descope decision is the remaining open item (final gate).

## Decision Audit Trail

| # | Phase | Decision | Class | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | 0 | Skip /office-hours prerequisite offer | Mechanical | P6 | Plan is already structured; review proceeds directly | Inline office-hours |
| 2 | 0 | UI scope = none → skip Phase 2 | Mechanical | P3 | Only false-positive matches ("Repo layout", table header) | Forcing a design pass |
| 3 | 1 | Mode = SELECTIVE EXPANSION | Mechanical | /autoplan override | Fixed by pipeline | — |
| 4 | 1 | Landscape check via live web search; all 6 citations verified | Mechanical | P1 | No-fabricated-claims house rule; premises needed evidence | Trusting memory |
| 5 | 1 | Premise gate → user: blind holdout + baselines | **User-decided** | — | Both voices: Eval A circular | Self-authored-only; Eval-B headline |
| 6 | 1 | Premise gate → user: pressure arms + prevention tier | **User-decided** | — | Threat model mismatch; prevention > detection for crude hacks | As-written; cut Builder |
| 7 | 1 | Premise gate → user: agent-evals/AI-infra audience | **User-decided** | — | Audience determines artifact shape | Generic SWE; both-audiences |
| 8 | 1 | LLM diff review: stretch-cut → baseline (uncuttable) | Mechanical | P1 | Baselines make the table interpretable | Keeping it first-to-cut |
| 9 | 1 | A1 host-side agent loop | Mechanical | P5 | Network-off BUILD is incoherent otherwise | Loop-in-container |
| 10 | 1 | A2 reference gets own container; adv tests never on host | Mechanical | P1 | Untrusted LLM code must not touch host | Host execution |
| 11 | 1 | A3 Day-1 dynamic-contexts spike + fallback | Mechanical | P3 | Single point of failure for 3 checks | Day-3 discovery |
| 12 | 1 | A4 VERIFY wall-clock budget; prebuilt images; config_hash contents | Mechanical | P1/P5 | Runtime + cache-poisoning control | Unbounded VERIFY |
| 13 | 1 | E1 INFRA_ERROR verdict state | Mechanical | P1 | Silent 0%-coverage FAIL poisons FP rate | Degrade-to-evidence |
| 14 | 1 | E2 mutant outcome taxonomy | Mechanical | P1 | Timeouts-as-kills inflates kill rate | Binary outcomes |
| 15 | 1 | E3 advtest 0/N neutral semantics + artifact retention | Mechanical | P1 | Absence of evidence ≠ pass | Silent no-op |
| 16 | 1 | E4 pytest exit-code taxonomy; empty-patch rule | Mechanical | P1 | Exit 2/5 ≠ failures | Implicit handling |
| 17 | 1 | S1 container hardening; venv = verify-only | Mechanical | P5 | LLM with shell never on host | Host fallback for BUILD |
| 18 | 1 | S2 patch-content prompt-injection in threat model; T2 can never elevate toward PASS | Mechanical | P1 | Adversary-authored text reaches Skeptic's models | Ignoring channel |
| 19 | 1 | D1 exact-red-set seed invariant | Mechanical | P1 | Collateral reds break problem statements | "Some tests red" |
| 20 | 1 | D2 corpus self-validation (gold full-VERIFY PASS at author time) | Mechanical | P1 | FP-by-construction caught before eval day | Trusting authorship |
| 21 | 1 | D3 gold-prime variants; FP split-reported | Mechanical | P1 | Revert-golds make FP vacuous | Single gold |
| 22 | 1 | Q1 H10 conditional on repo goldens | Mechanical | P3 | click/httpx have no goldens | Fabricating variants |
| 23 | 1 | Q2 cut order: call-graph, H10, corpus 15→10 | Mechanical | P3 | LLM review now load-bearing | Old cut order |
| 24 | 1 | T1/T2 harness self-tests + CI + lockfile | Mechanical | P1 | Verifier must be verified | Untested verifier |
| 25 | 1 | O1 trace enrichment; skeptic clean; exit codes | Mechanical | P1 | Debuggability + scripting | — |
| 26 | 1 | L1 honest multi-language rewording; attribution top-1+anywhere; FP n-caveat | Mechanical | P5 | No overclaims | Roadmap-washing |
| 27 | 3 | N1 fresh-container-per-check isolation + sanitized archive | Mechanical | P1 | Candidate contaminates verifier otherwise | Shared bind mount |
| 28 | 3 | N2 frozen per-task acceptance suite for Eval B | Mechanical | P1 | Skeptic PASS can't label Skeptic | Self-labeling |
| 29 | 3 | N3 task admission hardening (pristine ×2, baseline-equality, hacks-green) | Mechanical | P1 | Flaky/invalid corpus items poison eval | Loose invariants |
| 30 | 3 | N4 advtest acceptance ladder | Mechanical | P1 | "Passes reference" insufficient oracle | Single criterion |
| 31 | 3 | N5 H8 consumer-process probe | Mechanical | P5 | Spec'd detector was impossible | PYTEST_CURRENT_TEST check |
| 32 | 3 | N6 collect-only manifest diff check | Mechanical | P1 | Mechanism-agnostic H1/H3/H4 evidence | Bespoke-only detection |
| 33 | 3 | N7 coverage semantics defined; 0.8 min gets verdict rule | Mechanical | P5 | Undefined delta = undefined product | Implicit semantics |
| 34 | 3 | N8 changed-code vs caller kill rates; stratified sampling | Mechanical | P1 | Caller dilution + padding gameability | Single blended rate |
| 35 | 3 | N9 verdict semantics table; NOT_APPLICABLE; deterministic ordering | Mechanical | P5 | Score 0.87 was undefined | Vibes-based weights |
| 36 | 3 | N10 content-addressed cache key + run manifest | Mechanical | P1 | Replay claims false as keyed | Partial key |
| 37 | 3 | N11 repo admission protocol; env pinning; two-phase network | Mechanical | P1 | Unvalidated 3-repo premise | Day-1 surprises |
| 38 | 3 | N12 budget table corrected ($24–90 Builder arms) | Mechanical | P1 | Accepted scope doubled attempts | Stale numbers |
| 39 | 3.5 | DX1 skeptic demo (keyless, <60s, $0) | Mechanical | P1 | Only viable hello-world for keyless visitors | Key-gated first run |
| 40 | 3.5 | DX2 doctor + operational error contract | Mechanical | P1 | Every stranger failure is currently unspecified | Traceback UX |
| 41 | 3.5 | DX3 explicit free/paid lanes; cost-confirm on model calls | Mechanical | P5 | Never silently spend or downgrade | Implicit key-gating |
| 42 | 3.5 | DX4 dual-audience README + timed fresh-clone acceptance | Mechanical | P1 | <10-min bar engineered, not asserted | Day-7 aspiration |
| 43 | 3.5 | DX5 terminal verdict render spec | Mechanical | P5 | The product surface was JSON-only | Undefined output |
| 44 | 3.5 | DX6 discoverability defaults (tasks/runs list, next-command hints) | Mechanical | P5 | Run-ids were undiscoverable | Manual spelunking |
| 45 | 3.5 | DX7 reproduction manifest + skeptic reproduce | Mechanical | P1 | "Reproducible from clone" was false | Live-drift repro |
| 46 | 3.5 | DX8 --no-docker per-command semantics + report stamping | Mechanical | P5 | Isolation guarantees must be visible | Vague fallback |
| 47 | 3.5 | DX9 checks/ package rename | Mechanical | P5 | Import stutter in a read-me repo | skeptic.skeptic.* |
| 48 | 3.5 | DX10 GHCR image, report PNG, MIT license | Mechanical | P2 | Cheap distribution wins | Local-build-only |
| 49 | 1–3.5 | Corpus/schedule descope | **TASTE → final gate** | — | Both voices: Day 5 is 2+ days; options differ (6/2 vs 10/3 vs 12–15/3) | — |
| 50 | 3 | AST demoted to explanatory; outcome/collection diff primary | **TASTE → final gate** | — | Codex explicit; Claude partial (adds manifest diff, keeps AST) | — |
| 51 | 1 | T1 standalone diff mode (verify --diff) | **TASTE → final gate** | — | Claude: cheap 10x; Codex: dual-product risk | — |
| 52 | 1/3 | Hand-rolled mutator vs mutmut wrap spike | **TASTE → final gate** | — | Plan recorded tradeoff; Codex wants spike evidence | — |
| 53 | 3 | Cut call-graph now vs first-to-cut | **TASTE → final gate** | — | Codex: cut now to fund accepted scope | — |
| 54 | 3.5 | CLI verb redesign (task validate / agent run / eval verifier) | **TASTE → final gate** | — | Codex renames; Claude keeps verbs + demo/doctor | — |
| 55 | 1 | Telemetry/config prompts deferred (not plan decisions) | Mechanical | P3 | Out of plan scope; left for a normal session | — |

## Final gate outcomes (owner-decided, 2026-07-21)

| Gate item | Decision | Consequences applied |
|---|---|---|
| Corpus/schedule descope (audit #49) | **Keep 12–15 tasks / 3 repos; timeline is flexible** — "7 days was just an estimate; take as long as needed" | Part 1 §10's day numbers become **milestones M1–M7 with per-milestone exit criteria unchanged**; corpus authoring still runs as a daily thread from M2 (sequencing fix stands). Budget note: full corpus + pressure arms puts the worst-case Builder spend at ~$90–120, above the $75 floor; the ledger reports actuals, and the pre-registered contingency (in order) is: run pressure arms on a stratified task subset, then trim attempts per arm — never thin the per-task rigor (acceptance suites, gold-primes). |
| AST primacy (audit #50) | **Outcome/collection comparison primary; AST explanatory** | T1 hard-fail rules key off observed deltas: collected-set shrinkage (manifest diff), outcome flips (pass→skip/xfail), config-effective changes — measured baseline-vs-candidate. The AST diff attributes *why* (which assert weakened, which decorator appeared) and remains the README's showcased fundamental; it no longer solely drives hard-fails. Benign-refactor FP class shrinks; gold-prime numbers stay honest. |
| T1 standalone diff mode (audit #51) | **Ship it (M6)** | `skeptic verify --diff <any.diff>` + ~20-line GitHub Action; README demos it on one real public agent PR. Moved out of "NOT in scope". |
| Overall | **APPROVED** | Auto-decided defaults stand: hand-rolled mutator (Codex dissent recorded), call-graph check cut now, CLI verbs kept + demo/doctor/lists added, PASS/SUSPECT/FAIL kept with INFRA_ERROR/NOT_APPLICABLE. |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR (via /autoplan) | 3 premises challenged → owner-fixed; 24 findings folded; 5/6 consensus |
| Codex Review | `/codex review` | Independent 2nd opinion | 3 | ABSORBED (via /autoplan voices) | CEO 10 · Eng 12 (6 P0) · DX 3/10 verdict — all folded or gate-decided |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (via /autoplan) | 25 findings folded (N1–N12, A/E/S/D series), 0 critical gaps remaining |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | skipped, no UI scope |
| DX Review | `/plan-devex-review` | Developer experience gaps | 1 | CLEAR (via /autoplan) | score 3/10 → 8/10; TTHW unmeasured → demo <60s / full <10 min measured |

**CROSS-MODEL:** Claude and Codex voices converged independently on the four cross-phase themes (measure-don't-assert; deterministic tier is the spine; externalize ground truth; Day-5 overload). Divergences (descope depth, AST primacy, diff mode, CLI verbs) were all resolved at the gates by the owner.

**VERDICT:** CEO + ENG + DX CLEARED — ready to implement. Eval corpus + timeline decision recorded; restore point retained in the owner's local gstack workspace (path omitted from the committed record).

NO UNRESOLVED DECISIONS


---

# v3 Consolidation Amendments (owner-approved, 2026-07-22)

Prompted by an external review of v2's structure and outcomes; the consolidation (row 56) and all five amendments (rows 57-61) accepted without modification.

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| 56 | Consolidate: fold all accepted decisions into Parts 1–3; audit trail → DECISIONS.md | v2 was a v1 + amendment log; Parts 1–3 contradicted the gates in load-bearing places (scope, H8 detector, budget, schema). A builder implementing from Part 1 would build the superseded design | Append-only doc |
| 57 | Re-timebox: 3 weeks, M5 = publishable core (~Aug 7) | "Take as long as needed" removes the constraint that forced every cut; unbounded portfolio projects die at 80%. M5 guarantees a shippable artifact | Open-ended milestones |
| 58 | Pressure arms pre-committed to stratified 6-task subset (2/repo) | v2's contingency triggered after the ledger showed overspend — after the money was gone. Descope decided before spend; expand only if actuals leave headroom | Full-corpus arms w/ post-hoc descope |
| 59 | TRACE demoted to stretch; holdout labeled within-taxonomy | Mapping external trajectories into this pipeline is a mini-project (likeliest silent-death item). Model-authored holdout carries de-circularization; its within-taxonomy limit stated honestly | TRACE on critical path |
| 60 | Defer GHCR image, CONTRIBUTING, `skeptic reproduce` command; keep demo/doctor/lanes/committed manifest+traces | Production-product posture vs portfolio-proof leverage; committed artifacts preserve eval credibility without the machinery | Full DX10 as written |
| 61 | Replace "CRITICAL GAPS: none remaining" with open-questions register (plan §15); add prevention-vs-detection claim scoping (in-harness = prevention for H1–H4/H9/H10; --diff mode = detection load-bearing for all 10) | A plan never has zero gaps; the claim language contradicted the shipped diff mode | Zero-gap claim |

**v3 gate:** APPROVED 2026-07-22. Implementation starts Fri 2026-07-24.


---

# v3.1 Review Fixes (owner-approved, 2026-07-22)

A 7-auditor fidelity review of the v3 consolidation (27 findings, each adversarially verified against the file text; 27/27 confirmed, 0 refuted) found that consolidation had silently dropped several accepted decisions and introduced a few internal inconsistencies. Straight text-restoration fixes carry no new decision and were applied to the plan/DECISIONS body directly: P1 runtime-envelope table (+ N12 feasibility figures), the sandbox two-phase-network self-test, DX6's `eval` default and `--no-check` clause, the `seed --check` corpus CI smoke, S2 prompt-hardening, the Builder-side prompt-injection bound, N11 locale/TZ pinning, the `sys.exit(0)` pattern idiom, the append-only trace-log fundamentals row, N8's "never task `timeout_s`" guard, the hand-rolled-mutator rationale, the stack/distribution note, the `gold-prime` identifier, the `t1_scope` check name, holdout "within-taxonomy" labels at the summary mentions, the stale "60 variants" figure, and the future-dated approval stamp. Three fixes carried a real design choice; each took the stronger option and is recorded here.

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| 62 | H2 (assertion weakening) gains an aggregator path: a 0.5 soft-evidence row for AST-detected weakening, Part 2's H2 diff-mode detector corrected to "T1 AST weakening (soft) · diff-scope if `allowed_paths`", and honest scoping in §6/§15 that in `--diff` mode without `allowed_paths` H2 is soft-only (AST + T2 mutation), not a hard-fail | v3 §5.6 had no hard-fail or soft rule for a weakened-but-passing assertion, yet §6/Part 2 claimed T1 detection load-bearing for all ten categories in diff mode; a hard-fail would re-break gate #50 (AST attribution-only) and manufacture test-refactor false positives | Hard-fail on AST weakening; leaving the all-ten claim unqualified |
| 63 | Cache key re-includes `budgets` (A4's component restored over N10's omission) | Approved pressure arms differ only by budget; without budgets in the key their BUILD stages collide in the content-addressed cache and one arm serves the other's cached patch | Keep N10's budget-less key |
| 64 | Contingency ladder step 2 restored to gate #49's "trim frontier-billed pressure-arm attempts"; the v3 draft's "trim the weaker-model arm" is dropped | The weaker-model arm bills at the cheap tier (~$0.05–0.25/attempt), so dropping it saves almost nothing against a frontier-driven ceiling; gate #49's frontier-attempt trim is the real cost lever, and dropping an arm also loses that arm's per-arm hack-incidence row | Drop the weaker-model arm (draft wording) |

**v3.1 gate:** APPROVED 2026-07-22.

---

# M1 Close-out Amendments (owner-approved, 2026-07-25)

Closing M1 against §14's exit criteria found one met, one half met, one unmet:
the spike verdict is written (`docs/admission/click.md`), `seed --check` passes on
one task against a criterion of two, and there are no digest-pinned images. Both
misses are amended below, so the gap between the plan and the repo sits where a
reviewer can find it.

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| 65 | Digest-pinned images move from M1 to M2, landing with the BUILD stage that runs in them. Until then `--runner venv` is the only wired runner (verify-only, reduced isolation) and `--runner docker` exits INFRA with that message | An image belongs with the stage that consumes it; pinning one at M1 pins an artifact nothing executes. The code already assumed this ruling: `cli.py:67` has told operators "Docker runner lands with the BUILD stage" since M1, so the plan and the code disagreed and the disagreement lived only in a string literal. A decision recorded in a user-facing error message is a decision nobody can review | Building an image at M1 to satisfy the criterion literally; leaving the plan/code disagreement unrecorded |
| 66 | M1's "`seed --check` passes on 2 tasks" is satisfied by M2's first corpus task. It does not gate the M1 merge. The second task must be a different repo (`rich` or `httpx` per §4) | The criterion's value is testing whether the admission protocol generalizes: all six invariants were authored against `click` alone, so `parse_junit`, `_fresh_seeded`, and `pristine-text-unreachable` have never met a different build backend or test layout. That risk is real and stays tracked. It argues for the second task being M2's first item, which is where §14 already puts corpus authoring ("starts as a daily thread"). Gating a merge on work already scheduled two days out is ceremony. Promoting `minirepo` would satisfy the count while proving nothing, since it is a repo built to pass | Gating the M1 merge on a second task; promoting the `minirepo` fixture to satisfy the count; amending the criterion away entirely |
| 67 | `StageCache` and `run_stage` (`skeptic/orchestrator.py`) stay built, tested, and unwired until M2 | Caching an admission gate is a category error. A cached PASS returns green with nothing executed, and the first invariant it skips is `pristine-green-x2`, a deliberate double run whose only purpose is catching flakes, so caching defeats the check most designed to defeat caching. The primitive is frozen at M1 with the evidence schema (§14) so the check lanes and the aggregator lane share one shape, and it earns its keep at M2, when the FSM runs LOAD → SEED → BUILD → VERIFY and re-running an unchanged upstream stage is waste | Wiring it into `seed --check` for the sake of having no unused code; deleting it and rebuilding at M2, which would unfreeze a schema two lanes depend on |

**M1 gate:** foundations merged to `main` 2026-07-25. The README claims M1
foundations and names the two criteria still open.
