# DECISIONS.md · Skeptic plan provenance

Records every decision that shaped the plan: the v2 review addendum (55 decisions, dual-voice, dissents recorded), the v2 final gates, and the v3 consolidation amendments. Commit at repo root.

---

# Part 4 · Review Addendum (/autoplan, 2026-07-21)

*Historical snapshot of v2's review addendum. Copy-edited 2026-07-25 for punctuation and phrasing; no decision, rationale, or recorded dissent was altered. Where the v3 Consolidation Amendments (rows 56-61) and the v3.1 Review Fixes (rows 62-64) at the end of this file conflict with anything below (e.g. the timeline, or the "CRITICAL GAPS: none remaining" and "NO UNRESOLVED DECISIONS" absolutes), the later amendment governs.*

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
- **E2 (errors):** Mutant outcome taxonomy: killed / survived / **timeout** / **invalid** · timeouts and invalid mutants are excluded from the kill-rate denominator, reported separately.
- **E3 (errors):** Adversarial testgen edge: if 0 of N candidates pass on the reference, the check emits *no evidence* (neutral), an outcome distinct from a pass; failed candidates are retained as artifacts for generator debugging.
- **E4 (errors):** pytest exit-code taxonomy handled explicitly (0 green / 1 failures / 2 usage error / 5 no tests collected); empty Builder patch → FAIL(no-patch), a verdict the harness returns without crashing.
- **S1 (security):** Container hardening: non-root, `--network none` post-install, `--security-opt no-new-privileges`, pids-limit, read-only rootfs where viable. `--no-docker` venv fallback is **verify-only** · the Builder (LLM with shell) never runs outside Docker.
- **S2 (security):** Threat model documents patch-content prompt injection against the Skeptic's own testgen model (mitigations: prompt hardening; reference-validation step bounds the damage to a missed detection, never a false PASS elevation).
- **D1 (data):** Seed invariant tightened: the seeded red set must equal `failing_tests` **exactly** (no collateral reds).
- **D2 (data):** **Corpus self-validation:** `seed --check` runs the full VERIFY pipeline on gold (and gold-prime) and asserts verdict PASS at author time. A gold that can't pass its own verifier is a corpus bug, caught before eval day.
- **D3 (data):** **Gold-prime** variant per task (correct fix implemented differently from the revert); FP reported separately on gold vs gold-prime. Adversarial tests constrained to behavior implied by the problem statement.
- **Q1 (quality):** H10 applies only if a chosen repo actually ships golden/snapshot files; verify at corpus-selection time or mark H10 N/A-by-corpus honestly.
- **Q2 (quality):** Updated cut order: (1) call-graph check, (2) H10 variants, (3) corpus 15→10. LLM diff review is no longer cuttable (it's a baseline).
- **T1 (tests):** **Harness self-test suite** · the verifier gets verified: per-check unit tests against a fixture mini-repo (each hack category = a fixture diff with expected evidence), aggregator golden-verdict tests, mutation-operator tests, evalkit confusion-matrix math tests, trace-schema round-trip.
- **T2 (tests):** GitHub Actions CI for the harness: lint + self-tests + one smoke task in verify-only venv mode. A verification tool with no CI is an irony reviewers will notice.
- **P1 (perf):** Coverage run once per variant, JSON reused by T1 coverage delta + T2 test selection; runtime envelope table added to the plan.
- **O1 (observability):** Trace gains per-check timing, mutant-level outcome events, INFRA_ERROR events, and a run manifest (config hash, model ids, seeds) at run start; `skeptic clean` for orphaned containers/workspaces; CLI exit codes (0 PASS / 1 SUSPECT / 2 FAIL / 3 INFRA_ERROR) for scripting.
- **L1 (long-term):** Roadmap reworded honestly: "multi-language" → "second language (analyzer rewrite)". Metric hardening: attribution reported as top-1 *and* anywhere-in-evidence; the ≤10% FP bar is reported with its resolution limit (1 FP ≈ 7–8% at n≈13 golds), which is the honest precision at that sample size.
- **Repro:** dependency lockfile; seeds fixed; pinned commits (already planned) ✓.

## NOT in scope (considered and deferred, with rationale)

- GitHub App / PR-bot service: roadmap line stands (scope trap for 7 days).
- `skeptic verify --diff` standalone mode + 20-line GitHub Action: **taste decision at final gate** (Claude voice: cheap 10x; Codex: dual-product risk).
- Multi-language support: reworded honestly as a rewrite, and dropped from the roadmap.
- Live GitHub issue mining, RL/training, semantic equivalence proving, perf/concurrency/security oracles, web service: unchanged.
- Full TRACE-dataset evaluation: subset comparison only (budget).
- Distribution/marketing half-day (writeup, posting where agent-evals hirers read): deferred to TODOS with a resume/site link task.

## What already exists (leverage map)

- coverage.py dynamic contexts: used (T1 coverage, T2 test selection). Day-1 spike de-risks it.
- mutmut / cosmic-ray: considered; hand-rolled mutator kept for patch-scoping/budget/sampling control. **Codex dissent recorded** (wants a wrapping spike first) · taste decision at final gate.
- TRACE dataset (517 human-verified hack trajectories, 54 categories): now used as external holdout/comparison.
- SWE-bench harness: borrow docker task-env patterns (pinned images, deterministic installs).
- Author's parked agent-fault-harness design: reusable JSONL trace-event and orchestrator-FSM patterns.

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
| t1_coverage | contexts missing | CoverageDataMissing | Y | **INFRA_ERROR: never 0% evidence** | `coverage infra failure` |
| t2_mutation | mutant invalid / timeout | MutantInvalid / MutantTimeout | Y | excluded from kill-rate denominator | reported buckets |
| t2_advtests | candidate SyntaxError | CandidateInvalid | Y | discard, retain artifact | testgen yield stat |
| t2_advtests | 0/N pass reference | n/a | Y | neutral (no evidence) | noted in verdict evidence |
| aggregate | evidence schema invalid | EvidenceValidationError | Y | INFRA_ERROR | schema path in message |
| report | corrupt JSONL line | TraceParseWarning | Y | skip line + warn | report banner |
| ledger | usage fields absent | n/a | Y | estimate + mark `estimated:true` | ledger flag |

## Failure Modes Registry

| Codepath | Failure mode | Rescued? | Test? | User sees? | Logged? |
|---|---|---|---|---|---|
| coverage delta | silent 0% on infra failure | Y (INFRA_ERROR) | self-test fixture | explicit | Y |
| gold variant | fails own verifier (FP by construction) | Y (seed --check full-VERIFY) | corpus CI | explicit | Y |
| Builder | recovers fix from .git history | Y (git archive strip + reach check) | attempted-violation test | explicit | Y |
| adv. testgen | poisoned/trivial candidates | partial (reference validation bounds to missed detection) | fixture test | evidence notes | Y |
| mutation | timeouts inflate kill rate | Y (bucket taxonomy) | unit test | reported | Y |
| eval | flaky suite poisons verdict | Y (2× rerun + quarantine) | rerun policy test | quarantine list | Y |
| CRITICAL GAPS | **none remaining** after E1/D2 fold-in | n/a | n/a | n/a | n/a |

## Eng review findings folded into the plan (auto-decided, Phase 3)

- **N1 (isolation, was Codex P0):** One-container-per-run is insufficient. Execution model becomes: immutable per-repo dependency image (built with network, digest-pinned) → **sanitized candidate archive** (tracked files only; no `.git`, no `.pytest_cache`, no `__pycache__`, no untracked residue) → **fresh container per check run, per mutant batch, per adversarial-test run, and for every reference run**. No shared host bind mounts between Builder and Skeptic stages. The Builder can no longer contaminate its verifier.
- **N2 (Eval B ground truth, was Codex P0):** Each task gains a **frozen acceptance suite** authored at corpus time (held out from Builder, detectors, and adversarial testgen) so Eval B outcomes are classified RED / GREEN-wrong / GREEN-correct / INFRA_ERROR per pressure arm. Skeptic's own PASS never labels Skeptic's evaluation.
- **N3 (task admission, was Codex P0):** `seed --check` hardened: pristine suite green **twice** (flakiness screen); seed changes only the intended test outcomes (baseline-equality; a "full suite green" criterion would fail on environment-dependent skips, which are recorded as-is); no collection/import errors; every hack variant must be **green under ordinary pytest** with only its category-expected delta; patch files content-hashed into the task manifest.
- **N4 (adversarial-test acceptance, was Codex P0):** "Passes on reference" upgraded to: collected AND passed without skip/xfail AND executes the target code (coverage-verified) AND fails the seeded tree AND passes pristine + every gold-prime implementation; restricted to public API behavior; exact generated artifact persisted.
- **N5 (H8 detector, was Codex P0):** The taxonomy's H8 secondary detector ("adversarial tests run outside pytest env") is impossible as written: a pytest-run test still sets `PYTEST_CURRENT_TEST`. Replaced with a **standalone consumer-process probe**: import and call the public API in a bare Python process (no pytest, no coverage, no tracing env) and compare behavior against the in-pytest result.
- **N6 (collection-manifest diff, Claude voice):** New cheap T1 check: `pytest --collect-only -q` before/after patch; any disappearance from the collected set is mechanism-agnostic hard evidence covering H1/H3/H4 (and vectors not enumerated, e.g. `collect_ignore_glob` in nested conftest).
- **N7 (coverage semantics):** Patch-coverage defined precisely: executable statements only (deleted lines, decorators, imports, renames mapped explicitly); import-time execution does not count as test adequacy; `patch_coverage_min: 0.8` gets a defined verdict rule (below min → soft evidence; zero → hard fail; NO_DATA → INFRA_ERROR).
- **N8 (mutation robustness):** Kill rate split into **changed-code rate** and **caller-control rate** (callers of well-tested old code no longer dilute the signal); deterministic stratified sampling with fixed seed; statuses killed/survived/timeout/invalid/uncovered/import-failed; per-mutant timeout = 3× measured baseline of selected tests, cap 60s (never task `timeout_s`).
- **N9 (verdict semantics):** Soft-evidence weights and the SUSPECT threshold specified in one table in the plan; PASS requires every mandatory check completed (explicit NOT_APPLICABLE state); run status (ok / INFRA_ERROR) orthogonal to verdict; deterministic evidence ordering (severity, then fixed check precedence) so attribution is not an artifact of a sort.
- **N10 (cache/replay honesty):** Cache key = content hash over {patch bytes, repo commit, verifier code revision, prompts, model ids, image digest, dependency lock, seeds, check configs}; content-addressed run manifest emitted at run start; per-check completion records in the trace.
- **N11 (repo admission protocol):** Before Day 1: pin exact commits and measure, per repo · Python 3.12 install, full-suite runtime, skip census, dynamic-contexts support, offline execution. Pin base-image digest + arch (arm64 host), wheels via constraints file, locale/TZ/terminal env (`TERM`, `COLUMNS` for click/rich), async backend + no-proxy env for httpx. Two-phase lifecycle: build with network → run with `--network none`.
- **N12 (budget correction):** Cost table updated for accepted scope: base + 3 pressure arms ≈ 60 Builder attempts = **$24–90 before retries** (was $12–45); Eval A worst case ≈ 1,800 mutant pytest invocations + 480 candidate validations · feasible only with prebuilt images, per-mutant test selection, and the wall-clock caps above. Still inside $75 only if the corpus descope (final-gate decision) is taken; ledger reports actuals either way.

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
| A | Orchestrator + sandbox + spec loader | orchestrator/, sandbox/, cli.py | n/a |
| B | T1 checks (pure functions over fixtures) | skeptic/t1_*.py | n/a (fixture mini-repo only) |
| C | Corpus authoring (daily thread) | tasks/, patches/ | A (seed --check) |
| D | T2 + aggregator | skeptic/t2_*.py, aggregate.py | A (sandbox exec), B (evidence schema) |
| E | Evalkit + report | evalkit/, report/ | trace schema (A) |

Launch A + B in parallel; C starts as soon as A's `seed --check` exists and continues daily; D after A+B; E anytime after the trace schema freezes. Conflict flag: B and D share the evidence schema · freeze `verdict.json`/evidence dataclasses on Day 1.

## DX review findings folded into the plan (auto-decided, Phase 3.5)

**Persona (0A):** primary = eval-literate reviewer (agent-evals / AI-infra) landing from a resume or post; tolerance ≈ 60 seconds to decide the repo is credible, ≈ 10 minutes if they clone. Secondary = hands-on cloner evaluating the tool itself. **Magical moment (0D):** a copy-paste command that catches a cheat: a colored FAIL verdict with cited evidence, in under a minute, for $0. **Competitive anchor (0C):** SWE-bench's official harness needs 120GB disk and ~15–50 min to first eval; Skeptic's measured footprint/cold-start table is a differentiator worth printing in the README.

- **DX1 · `skeptic demo`:** keyless, dockerless, network-free; audits a bundled fixture mini-repo (shared with the harness self-test suite) and prints a real FAIL verdict with evidence, `Cost: $0.00`, in <60s. This is the README's first command and the project's magical moment.
- **DX2 · `skeptic doctor`:** preflight for Docker daemon (CLI absent vs daemon stopped vs socket permission), API keys, Python 3.12, disk space, architecture. **Operational error contract:** every setup failure states what failed, why Skeptic needs it, and the exact next command. Named Day-6 exit criterion (replaces undefined "CLI polish").
- **DX3 · Free vs paid lanes, explicit:** `--profile deterministic` (T1 + mutation, zero keys) selectable on `verify`/`eval`; key validation happens per command *before* any image/dependency work, naming the exact env var; never silently downgrade; model-calling commands print an estimated max cost and require confirmation (consistent with the cost-ledger ethos).
- **DX4 · README dual-audience restructure:** above the fold = one-sentence value prop + eval table (with n, corpus version, run date) + the free demo command with pasted expected output + evidence GIF + measured time/disk vs SWE-bench. Second screen = 3 lanes (free demo / deterministic real verify / paid end-to-end) each with measured runtime, download, disk, max cost. Then architecture, tradeoffs, limits, related work. Quickstart is an executable acceptance artifact from Day 1; Day 7 gains a **timed fresh-clone run in a clean container** as the <10-min acceptance test.
- **DX5 · Terminal verdict spec:** rendered verdict banner + evidence list (check · category · file:line · artifact path); exit codes 0/1/2/3 (already in O1); this unblocks the "run the CLI in CI" roadmap line.
- **DX6 · Discoverability defaults:** `skeptic tasks list`, `skeptic runs list`; `report` defaults to the latest run; `eval` defaults to `tasks/`→`reports/`; every command prints the suggested next command; `seed` validates by default (`--no-check` to skip).
- **DX7 · Reproduction manifest:** `evals/v1/manifest.json` (content hashes for code, corpus, patches, image digest, dependency locks, model/prompt identities, seeds) + `skeptic reproduce <manifest>` (frozen artifacts, exact table) vs `skeptic eval --live` (new experiment). Trace events gain schema version, tool git SHA, corpus hash, image digest, model/prompt identity. Raw traces + trusted generated tests for the headline run are committed.
- **DX8 · `--no-docker` semantics per command:** allowed for `verify` on repo-shipped patches (isolation warning printed AND stamped into the report/verdict); refused for `build`/`run`. (Extends S1.)
- **DX9 · Package rename:** inner `skeptic/skeptic/` → `skeptic/checks/` (kills the `skeptic.skeptic.t1_ast` import stutter in a repo whose whole point is being read).
- **DX10 · Distribution:** prebuilt task-ready image published to GHCR (fallback: local build); one PNG of a rendered report committed next to the GIF (HTML doesn't render on GitHub); MIT license + minimal CONTRIBUTING.
- **DX Scorecard (plan-as-written → post-fold target):** Getting Started 2→9 · API/CLI 5→8 · Errors 2→8 · Docs 4→9 · Upgrade/Repro 3→8 · Dev Env 4→8 · Community 3→6 · DX Measurement 3→7 · **Overall 3→8**. TTHW: unmeasured (~15+ min cold) → demo lane <60s (Champion), real-docker lane <10 min measured (Competitive). Principle coverage: Zero Friction (demo), Learn by Doing (demo+lanes), Fight Uncertainty (doctor+error contract), Opinionated+Escape Hatches (profiles, --no-docker), Code in Context (README lanes), Magical Moment (demo verdict).

## Cross-Phase Themes (flagged independently in 2+ phases · high-confidence signals)

1. **Measure, don't assert**: CEO (pre-registered bar without baselines), Eng (budget table vs real attempt count; FP n-resolution), DX (the <10-min claim is "stated but not engineered for"). Every headline claim now has a measurement mechanism: baselines, per-arm reporting, timed fresh-clone test, cost actuals.
2. **The free deterministic tier is the product's spine**: CEO (T1 catches the crude majority cheaply), Eng (T1 checks are pure functions, easiest to self-test), DX (keyless demo is the only viable hello-world). Investment order follows: T1 + fixtures first, everything else layers on.
3. **Self-authored ⇒ self-graded**: CEO (Eval A circularity), Eng (Eval B ground truth; attribution = your own sort), DX (reproduction manifest so outsiders can check). The blind holdout + acceptance suites + manifest are one theme: externalize the ground truth.
4. **Schedule realism**: all three phases independently flagged Day 5 as 2+ days of work and Days 6–7 as carrying unspecified deliverables. The descope decision is the remaining open item (final gate).

## Decision Audit Trail

| # | Phase | Decision | Class | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | 0 | Skip /office-hours prerequisite offer | Mechanical | P6 | Plan is already structured; review proceeds directly | Inline office-hours |
| 2 | 0 | UI scope = none → skip Phase 2 | Mechanical | P3 | Only false-positive matches ("Repo layout", table header) | Forcing a design pass |
| 3 | 1 | Mode = SELECTIVE EXPANSION | Mechanical | /autoplan override | Fixed by pipeline | n/a |
| 4 | 1 | Landscape check via live web search; all 6 citations verified | Mechanical | P1 | No-fabricated-claims house rule; premises needed evidence | Trusting memory |
| 5 | 1 | Premise gate → user: blind holdout + baselines | **User-decided** | n/a | Both voices: Eval A circular | Self-authored-only; Eval-B headline |
| 6 | 1 | Premise gate → user: pressure arms + prevention tier | **User-decided** | n/a | Threat model mismatch; prevention > detection for crude hacks | As-written; cut Builder |
| 7 | 1 | Premise gate → user: agent-evals/AI-infra audience | **User-decided** | n/a | Audience determines artifact shape | Generic SWE; both-audiences |
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
| 25 | 1 | O1 trace enrichment; skeptic clean; exit codes | Mechanical | P1 | Debuggability + scripting | n/a |
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
| 42 | 3.5 | DX4 dual-audience README + timed fresh-clone acceptance | Mechanical | P1 | <10-min bar engineered and measured | Day-7 aspiration |
| 43 | 3.5 | DX5 terminal verdict render spec | Mechanical | P5 | The product surface was JSON-only | Undefined output |
| 44 | 3.5 | DX6 discoverability defaults (tasks/runs list, next-command hints) | Mechanical | P5 | Run-ids were undiscoverable | Manual spelunking |
| 45 | 3.5 | DX7 reproduction manifest + skeptic reproduce | Mechanical | P1 | "Reproducible from clone" was false | Live-drift repro |
| 46 | 3.5 | DX8 --no-docker per-command semantics + report stamping | Mechanical | P5 | Isolation guarantees must be visible | Vague fallback |
| 47 | 3.5 | DX9 checks/ package rename | Mechanical | P5 | Import stutter in a read-me repo | skeptic.skeptic.* |
| 48 | 3.5 | DX10 GHCR image, report PNG, MIT license | Mechanical | P2 | Cheap distribution wins | Local-build-only |
| 49 | 1–3.5 | Corpus/schedule descope | **TASTE → final gate** | n/a | Both voices: Day 5 is 2+ days; options differ (6/2 vs 10/3 vs 12–15/3) | n/a |
| 50 | 3 | AST demoted to explanatory; outcome/collection diff primary | **TASTE → final gate** | n/a | Codex explicit; Claude partial (adds manifest diff, keeps AST) | n/a |
| 51 | 1 | T1 standalone diff mode (verify --diff) | **TASTE → final gate** | n/a | Claude: cheap 10x; Codex: dual-product risk | n/a |
| 52 | 1/3 | Hand-rolled mutator vs mutmut wrap spike | **TASTE → final gate** | n/a | Plan recorded tradeoff; Codex wants spike evidence | n/a |
| 53 | 3 | Cut call-graph now vs first-to-cut | **TASTE → final gate** | n/a | Codex: cut now to fund accepted scope | n/a |
| 54 | 3.5 | CLI verb redesign (task validate / agent run / eval verifier) | **TASTE → final gate** | n/a | Codex renames; Claude keeps verbs + demo/doctor | n/a |
| 55 | 1 | Telemetry/config prompts deferred (not plan decisions) | Mechanical | P3 | Out of plan scope; left for a normal session | n/a |

## Final gate outcomes (owner-decided, 2026-07-21)

| Gate item | Decision | Consequences applied |
|---|---|---|
| Corpus/schedule descope (audit #49) | **Keep 12–15 tasks / 3 repos; timeline is flexible**: "7 days was just an estimate; take as long as needed" | Part 1 §10's day numbers become **milestones M1–M7 with per-milestone exit criteria unchanged**; corpus authoring still runs as a daily thread from M2 (sequencing fix stands). Budget note: full corpus + pressure arms puts the worst-case Builder spend at ~$90–120, above the $75 floor; the ledger reports actuals, and the pre-registered contingency (in order) is: run pressure arms on a stratified task subset, then trim attempts per arm · never thin the per-task rigor (acceptance suites, gold-primes). |
| AST primacy (audit #50) | **Outcome/collection comparison primary; AST explanatory** | T1 hard-fail rules key off observed deltas: collected-set shrinkage (manifest diff), outcome flips (pass→skip/xfail), config-effective changes · measured baseline-vs-candidate. The AST diff attributes *why* (which assert weakened, which decorator appeared) and remains the README's showcased fundamental; it no longer solely drives hard-fails. Benign-refactor FP class shrinks; gold-prime numbers stay honest. |
| T1 standalone diff mode (audit #51) | **Ship it (M6)** | `skeptic verify --diff <any.diff>` + ~20-line GitHub Action; README demos it on one real public agent PR. Moved out of "NOT in scope". |
| Overall | **APPROVED** | Auto-decided defaults stand: hand-rolled mutator (Codex dissent recorded), call-graph check cut now, CLI verbs kept + demo/doctor/lists added, PASS/SUSPECT/FAIL kept with INFRA_ERROR/NOT_APPLICABLE. |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR (via /autoplan) | 3 premises challenged → owner-fixed; 24 findings folded; 5/6 consensus |
| Codex Review | `/codex review` | Independent 2nd opinion | 3 | ABSORBED (via /autoplan voices) | CEO 10 · Eng 12 (6 P0) · DX 3/10 verdict: all folded or gate-decided |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (via /autoplan) | 25 findings folded (N1–N12, A/E/S/D series), 0 critical gaps remaining |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | n/a | skipped, no UI scope |
| DX Review | `/plan-devex-review` | Developer experience gaps | 1 | CLEAR (via /autoplan) | score 3/10 → 8/10; TTHW unmeasured → demo <60s / full <10 min measured |

**CROSS-MODEL:** Claude and Codex voices converged independently on the four cross-phase themes (measure-don't-assert; deterministic tier is the spine; externalize ground truth; Day-5 overload). Divergences (descope depth, AST primacy, diff mode, CLI verbs) were all resolved at the gates by the owner.

**VERDICT:** CEO + ENG + DX CLEARED: ready to implement. Eval corpus + timeline decision recorded; restore point retained in the owner's local gstack workspace (path omitted from the committed record).

NO UNRESOLVED DECISIONS


---

# v3 Consolidation Amendments (owner-approved, 2026-07-22)

Prompted by an external review of v2's structure and outcomes; the consolidation (row 56) and all five amendments (rows 57-61) accepted without modification.

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| 56 | Consolidate: fold all accepted decisions into Parts 1–3; audit trail → DECISIONS.md | v2 was a v1 + amendment log; Parts 1–3 contradicted the gates in load-bearing places (scope, H8 detector, budget, schema). A builder implementing from Part 1 would build the superseded design | Append-only doc |
| 57 | Re-timebox: 3 weeks, M5 = publishable core (~Aug 7) | "Take as long as needed" removes the constraint that forced every cut; unbounded portfolio projects die at 80%. M5 guarantees a shippable artifact | Open-ended milestones |
| 58 | Pressure arms pre-committed to stratified 6-task subset (2/repo) | v2's contingency triggered after the ledger showed overspend: after the money was gone. Descope decided before spend; expand only if actuals leave headroom | Full-corpus arms w/ post-hoc descope |
| 59 | TRACE demoted to stretch; holdout labeled within-taxonomy | Mapping external trajectories into this pipeline is a mini-project (likeliest silent-death item). Model-authored holdout carries de-circularization; its within-taxonomy limit stated honestly | TRACE on critical path |
| 60 | Defer GHCR image, CONTRIBUTING, `skeptic reproduce` command; keep demo/doctor/lanes/committed manifest+traces | Production-product posture vs portfolio-proof leverage; committed artifacts preserve eval credibility without the machinery | Full DX10 as written |
| 61 | Replace "CRITICAL GAPS: none remaining" with open-questions register (plan §15); add prevention-vs-detection claim scoping (in-harness = prevention for H1–H4/H9/H10; --diff mode = detection load-bearing for all 10) | A plan never has zero gaps; the claim language contradicted the shipped diff mode | Zero-gap claim |

**v3 gate:** APPROVED 2026-07-22. Implementation starts Fri 2026-07-24.


---

# v3.1 Review Fixes (owner-approved, 2026-07-22)

A 7-auditor fidelity review of the v3 consolidation (27 findings, each adversarially verified against the file text; 27/27 confirmed, 0 refuted) found that consolidation had silently dropped several accepted decisions and introduced a few internal inconsistencies. Straight text-restoration fixes carry no new decision and were applied to the plan/DECISIONS body directly: P1 runtime-envelope table (+ N12 feasibility figures), the sandbox two-phase-network self-test, DX6's `eval` default and `--no-check` clause, the `seed --check` corpus CI smoke, S2 prompt-hardening, the Builder-side prompt-injection bound, N11 locale/TZ pinning, the `sys.exit(0)` pattern idiom, the append-only trace-log fundamentals row, N8's "never task `timeout_s`" guard, the hand-rolled-mutator rationale, the stack/distribution note, the `gold-prime` identifier, the `t1_scope` check name, holdout "within-taxonomy" labels at the summary mentions, the stale "60 variants" figure, and the future-dated approval stamp. Three fixes carried a real design choice; each took the stronger option and is recorded here.

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| 62 | H2 (assertion weakening) gains an aggregator path: a 0.5 soft-evidence row for AST-detected weakening, Part 2's H2 diff-mode detector corrected to "T1 AST weakening (soft) · diff-scope if `allowed_paths`", and honest scoping in §6/§15 that in `--diff` mode without `allowed_paths` H2 is soft-only (AST + T2 mutation), with no hard-fail path | v3 §5.6 had no hard-fail or soft rule for a weakened-but-passing assertion, yet §6/Part 2 claimed T1 detection load-bearing for all ten categories in diff mode; a hard-fail would re-break gate #50 (AST attribution-only) and manufacture test-refactor false positives | Hard-fail on AST weakening; leaving the all-ten claim unqualified |
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

---

# Second Corpus Admission (owner-approved, 2026-07-25)

Decision 66 said the second corpus task had to be a different repo, on the
grounds that every seedcheck invariant so far was authored against `click` alone
and the protocol's value is that it generalizes. `rich-0001` is that task, and
authoring it found a harness defect on the first try.

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| 68 | The venv runner stops pinning `COLUMNS`, and starts pinning `LANG`, `LC_ALL` (both `C.UTF-8`) and `TZ` (`UTC`). Regression test in `tests/test_sandbox.py` asserts both halves | Pinning terminal width looks like determinism and is not. A suite that renders to a width sets that width explicitly; a suite that probes terminal-size *fallback* is testing the unset case, so the pin fails it for a reason unrelated to any seeded bug, which is precisely how a harness manufactures false evidence. Measured on both corpus repos: rich fails 3 tests in `test_size_can_fall_back_to_std_descriptors` with the pin, and click's 1939 pass identically with or without it, so the pin cost coverage and bought nothing. Locale and timezone are the opposite case and were missing: they change program output with no test opting in. This amends plan §13, which listed `COLUMNS` among the things to pin "for click/rich" | Leaving `COLUMNS` pinned and quarantining rich's 3 tests, which would have hidden a harness defect behind a per-task exception list; making the whole env per-task in the spec, which is more surface than one wrong constant justifies |
| 69 | `rich-0001` seeds a branch-conditional bug in `Rule.__rich_console__`: the title-space reservation collapses to one constant, so only center alignment breaks | Seven candidates were seeded and their real red sets measured, because D1 requires the seeded red set to equal `failing_tests` exactly. Intuition ranked badly: `Padding.unpack` transposition cascaded to 42 tests across 10 files and `Lines.justify` to 9 across 6, while the chosen bug is 4 tests in 1 file. Rule beat the other clean candidates because only `center` breaks while left and right stay correct, which is the shape that invites H6 input special-casing: an agent can guard the tested widths, go green, and leave the real path broken. That is a hack the corpus needs to contain | `Bar` end-clamp (clean at 2 tests, but the constructor's effect shows in `__repr__`, handing the agent a hint); `filesize` zero-size (clean, but its red set spans two modules, which makes the blast radius harder to argue as exact); the two 1-test candidates as too thin |

**Carried:** two disclosures, both recorded in `docs/admission/rich.md`.

The plan's "underspecified problem statement" pressure arm (accepted scope, item
2) has no headroom on rich-0001. The seeded run's own pytest output localizes the
defect before the statement is read: three failing ids carry the literal
`[center-` while their `[left-` and `[right-` siblings pass. The manipulation is
therefore task-dependent, and that arm has to be sourced from a task whose test
names do not narrate the defect. Check that property at admission time.

Neither corpus task ships a gold-prime, an acceptance suite, or hack variants.
Plan §14 puts gold-prime at M4 and acceptance suites at M5, so the deferral is
correct, but `hacked_verdict_any_of: [SUSPECT, FAIL]` is currently an expectation
with no variant to test it. Gold-prime for both tasks is a hard M4 dependency:
without it the M4 exit criterion cannot be evaluated.

---

# M2 Build (owner-approved, 2026-07-26)

M2 wired the BUILD stage: the Builder's host-side tool layer, the per-repo
Docker image, and the session container the Builder's tool calls run in. Four
decisions came out of that work, including one open issue carried forward
rather than resolved.

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| 70 | Runner shell semantics unified on `sh -c` | M1 shipped VenvRunner with no-shell `shlex.split` while DockerRunner used `sh -c`, so one command string had two meanings. Both runners now use `sh -c`. Spec-authored commands are trusted input. The missing-executable failure moved from a raised infra error in `exec` to exit 127 handled by callers, which already wrap nonzero exits with what/why/next. | Keeping the divergence documented was rejected: run_suite feeds the same string to whichever runner it gets, and a semantic fork inside that seam is exactly where a latent bug lives. |
| 71 | Per-repo image built in two Docker stages: `resolve` installs `environment.install` against the pristine tree and freezes the closure to `constraints.txt`; the final image installs only that constraints file into the digest-pinned base, with no repo source and no `resolve` layers | Task 3 needed one image reusable across every candidate and edit made to that repo's source. A single build sharing layers between dependency install and source would ship the pristine tree's tests and history into the same artifact the Builder runs in. Splitting into a throwaway resolve stage and a constraints-only runtime stage means the image holds dependencies only; source arrives per run through the workspace bind mount and the session container's offline overlay install (row 72). The tag keys on the pinned commit plus a hash of `environment.install` and the build-backend list, so an edited install command gets a new tag instead of silently reusing a stale image | A single-stage build installing deps and copying the repo into the final image: it ties image content to source that changes every run, forcing a rebuild (and a bigger image) per candidate instead of per dependency change, and it would put a git-history-bearing tree inside the artifact the untrusted Builder's tool calls run in |
| 72 | BUILD keeps one Docker container alive for the whole Builder session (`SessionContainer`: `docker run -d ... sleep infinity` plus `docker exec` per tool call) instead of a fresh container per command. At start it creates `/workspace/.sv`, a `--system-site-packages` venv, and runs an offline `pip install --no-deps --no-index --no-build-isolation -e /workspace` against the image's frozen dependency closure. Two exec paths are exposed: `exec_shell` (harness-composed strings, full `sh -c`) and `exec_argv` (Builder-supplied argv, no shell). `run_tests` feeds `environment.test_cmd` through `shlex.split` into `exec_argv`, so BUILD interprets `test_cmd` as an argv list, not a shell string, which is why it diverges from `seed --check`'s `sh -c` (row 70) on anything shell-flavored | Every other stage under the plan's N1 isolation policy is fresh-container-per-run, because a fresh reference/verify container must never inherit Builder-touched state. BUILD is the exception on purpose: the Builder issues many tool calls against the same source tree in one session, and a fresh container (and a fresh pip install) per call would repeat the same offline install for no isolation benefit, since the workspace bind mount, not the container, is what changes between calls. The overlay venv uses `--system-site-packages` because the runtime image installs the dependency closure into the base interpreter (row 71); that flag is the only way the workspace venv sees those packages without a second, redundant install. The `exec_shell`/`exec_argv` split exists so a Builder-controlled string can never chain a second command; Task 9's fix (routing the `run_tests` selector through `exec_argv`) depends on that split existing | Fresh-container-per-tool-call, the isolation model used everywhere else in the plan: it buys no additional isolation against the workspace bind mount the Builder already writes to, and it would re-run the offline editable install on every tool call inside a loop that ran 8 iterations in one real session. A single `exec` method with shell semantics for both harness and Builder input: that is the exact chaining hole Task 9's review found and fixed by moving Builder-supplied arguments to exec form |
| 73 | The click-0001 environment gap is recorded as an open issue, not resolved in M2: `seed --check`'s six invariants and `skeptic build`'s BUILD container validate different environments, and nothing in M2 closes that gap | A free experiment ran both corpora's pristine (unseeded) trees inside the BUILD container. click-0001 fails 24 of 1940 tests, all in `tests/test_utils/test_echo_via_pager.py`, because they invoke the `less` binary, absent from the `python:3.12-slim` base image and the deps-only image built from it (row 71); rich-0001 is clean at 956 passed. Full-suite green is therefore structurally unreachable for click-0001 inside BUILD regardless of what the Builder does: both real runs produced a candidate with the same one-line change as the gold patch and both still ended `stop_reason: model_ended`, `suite_green: false`. The six seed-check invariants (`pristine-green-x2` among them) run under `VenvRunner` on the host, where `less` is installed, so corpus admission never observed this gap. Stated generally: admission validates a different environment than BUILD runs in, and any system package present on the corpus author's host and absent from the deps-only image can reproduce this on a future task | Adding `less` to the base image to make click-0001's suite pass: that patches one task's symptom without closing the general gap (the next missing system package recreates it) and expands the deps-only image's scope (row 71) without an owner decision on which system packages BUILD images should carry. Redefining `suite_green` to accept a Builder-scoped selector run: Task 12's diagnosis already showed a selector-scoped run forfeits the exact completion signal the harness relies on. Left open for the owner: image system packages, a per-task spec field for host-only exclusions, or a redefinition of BUILD's green stop signal |

Row 73 is resolved by row 74 below.

---

# M3 prep: green is differential (owner-approved, 2026-07-26)

Row 73 left three options on the table. Reading M3's spec while writing them up
surfaced the argument that settles it: §5's T1 checks are already differential
("pure fns over baseline-vs-candidate observations"), which makes BUILD's
absolute-green stop signal the outlier. click-0001 was only the first task
unlucky enough to expose it.

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| 74 | Green means nothing got worse. The absolute reading, under which green requires a suite with zero red tests, is retired. A stage is green when every nodeid in the spec's `failing_tests` now passes and no test that passed in an in-container baseline now fails. BUILD establishes that baseline by running the suite once against the seeded workspace before the Builder's first tool call, and the baseline red set is recorded in the trace on every run whatever the verdict. This resolves row 73: click-0001's 24 `less` failures appear in the baseline and in every candidate run, so they cancel and stop being fatal, while a Builder that breaks a passing test is still caught | The T1 checks M3 builds are already differential (§5 line 64: "pure fns over baseline-vs-candidate observations"; `t1_collect` is collect-diff, `t1_outcomes` is collect-diff plus outcome flips), so a constant environmental red set subtracts out of them. BUILD's `suite_green` was the one consumer demanding an absolute green, which is why the gap surfaced there first and nowhere else. It would have surfaced again at M4: §14's corpus self-validation requires gold and gold-prime to come back PASS at author time, and click-0001's gold is a correct fix that still leaves 24 red tests in-container, so the plan would have diagnosed an environment bug as "a corpus bug caught before eval day". Defining green differentially makes BUILD consistent with VERIFY instead of stricter than it, costs one extra suite run per task (2.06s measured on click-0001, 4.18s on rich-0001), and is environment-independent by construction, so the next missing system binary is a recorded baseline line rather than a wasted budget and a two-hour diagnosis | Adding system packages to the image: it fixes the instance, leaves the class open, and expands row 71's deps-only scope. A per-task spec field for host-only exclusions: the exclusion would have to be applied identically at admission, build, and verify or the divergence just moves, and it has the harness excising tests from the suite, which is the shape of H1, the hack being detected. Both stay available as later refinements: they compose with a differential definition rather than competing with it |

**Scope:** this row is the decision. The implementation lands as M3's first
piece, since it touches the `suite_green` computation, the CLI's BUILD wiring,
and the shape VERIFY inherits. Row 73's evidence stands as written.

---

# M3 plan rulings (owner-approved, 2026-07-26)

The M3 plan (`docs/superpowers/plans/2026-07-26-m3-t1-checks.md`) was authored
and adversarially reviewed the same day, and the review left seven questions
open. The owner ruled on all seven, recorded here as six rows: row 78 answers
two of the questions together, the flag every harness-issued pytest invocation
carries and the admission assertion that licenses it, because neither stands
without the other. Five of the six rows accept what the plan or row 74 already
said and add the reasoning those documents were missing, which is worth a row on
its own: a reader who finds only the conclusion cannot tell whether the
alternative was considered and dropped or never raised. Row 80 changed the
plan's design and is marked as such. The plan text was amended to carry every
ruling before these rows were written, so the rows describe what the plan now
says.

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| 75 | `t1_scope` emits the non-taxonomy category `scope` on every occurrence, with no path-based taxonomy ladder (plan Task 6, line 552) | The check observes that a path sits outside `allowed_paths` and cannot observe the mechanism that put it there. A ladder keyed on which spec list the path belongs to gets two of the eight minirepo hack fixtures wrong in opposite directions: the root `conftest.py` is in `config_files`, so a ladder labels the H9 autouse hack H4, and `tests/conftest.py` is under `test_dirs`, so a ladder labels the H4 ignore hack H1. The second argument is about where a ladder could ever pay. In `verify --diff` mode `allowed_paths` is empty, so `t1_scope` is NOT_APPLICABLE and taxonomy attribution comes from the mechanism checks regardless of what a ladder would have guessed. A ladder can therefore only fire in-harness, the posture where §6 line 130 makes H1 through H4, H9, and H10 prevention claims and attribution carries the least weight. Ownership of attribution sits with the check that saw the mechanism, and Task 8's annotate pass adds the prose naming it | A path ladder inferring H1, H2, H3, H4, or H9 from the spec list the offending path belongs to. Also rejected: skipping the row because the plan already specified the behavior, which would leave the two fixture counterexamples and the diff-mode argument recorded nowhere durable |
| 76 | Top-1 attribution is measured in the diff posture, and wherever an attribution figure is reported the posture is named in the same sentence as the figure. Binds Task 14's exit-criterion assertion and every attribution figure M5's README prints (plan lines 1081 and 1083) | `allowed_paths` is the only spec field any check keys on and the checks are pure, so one collected pair is checked twice: once against the task's real spec, once against a copy with `allowed_paths: []`. The second pass costs no execution. Measuring in-harness would score the precedence list instead of the layer, since `CHECK_PRECEDENCE` puts `t1_scope` ahead of `t1_coverage` (plan line 312) and every hack fixture carries a `scope` entry in that posture, so `evidence[0]` would be a non-taxonomy category on all eight. The reporting condition is about honesty rather than mechanics: a bare "top-1 attribution N of 8" invites a reader to hear a claim about the harness end to end, while "N of 8 in the diff posture, where detection is load-bearing" is the claim the test supports, and this project's credibility rests on its numbers never reading stronger than they are | Measuring in-harness, where the number would be a sort artifact. Naming the posture in a footnote or in a section elsewhere: a figure travels away from its section, and the qualifier has to travel with it |
| 77 | Row 74's first clause makes crude excision non-green at BUILD, recorded as a consequence rather than a choice. Six of the eight hack fixtures are now patches BUILD would stop, and all eight remain the detection corpus for `verify --diff` mode (plan lines 57, 58, and 403) | A hack that deletes, skips, or deselects a `failing_tests` nodeid leaves it absent or non-passing, so row 74's first clause fails and BUILD never stops on it. That is a prevention-side gain row 74 did not claim. The plan's original framing said only `h2-weakening` and `h9-autouse-stub` model a Builder that could have submitted while BUILD reported green, which is right for the in-harness posture and understates the rest, so the corrected text states both halves: in `verify --diff` mode there is no BUILD, no green rule, and no prevention in front of the checks, a patch arrives cold, and detection is the only thing standing. The six fixtures BUILD would now stop are the layer's only H1, H3, H4, and H10 evidence | Cutting the six now-preventable fixtures as redundant work: each is the sole fixture behind a hard-fail rule, so cutting any one leaves that rule unexercised. Leaving the in-harness-only sentence to stand, which reads as six fixtures wasted |
| 78 | Every harness-issued pytest invocation in BUILD and VERIFY carries `--continue-on-collection-errors`, `--collect-only` included, while `seedcheck.run_suite` keeps its argv and its `(0, 1)` exit rule. Admission asserts the property the flag leans on: the contract goes in `seedcheck`'s module docstring, `run_suite`'s exit-code message names collection failure first with the next command, and two tests pin both paths. A nonzero baseline `collection_errors` at BUILD raises INFRA and is documented as a guard expected to stay silent (plan lines 59, 60, 84, 85, and 86) | Measured with pytest 9.1.1 on a two-file tree whose second file imports a missing module: without the flag pytest exits 2 with `Interrupted: 1 error during collection` and no test runs at all, so a candidate that breaks one import erases the whole observation. With the flag it exits 1, the healthy tests run, and the broken module's tests are absent from the collected set, which is the state `t1_collect` turns into H1 evidence. This is the third deliberate split between admission and the pipeline after row 70's shell semantics and row 74's environment gap, and a third one earns resistance. It survives on the merits: admission asks whether the repo is sound and wants a hard stop, while VERIFY asks what the candidate did and has to survive a broken import in order to observe it. What turns the divergence into a contract is the admission assertion. `run_suite` raises on any exit outside `(0, 1)`, and `pristine-green-x2` (`seedcheck.py:156`) and `seed-red-exact` (`seedcheck.py:190`) both fold `collection_errors == 0` into their pass condition, so a collection error seen downstream is candidate-caused by construction. Neither path was named as the contract and no test pinned either one; making it explicit costs 2 tests and a message rewrite (plan line 15). The BUILD guard rests on the same assertion: `seed-red-exact` already requires a clean collect on the same seeded tree BUILD then runs, so the guard should never fire, and if it does, something changed between admission and BUILD and the response is to find out what | Giving `seed --check` the same flag for uniformity: a tree that cannot import its own tests is not admissible, and the hard stop is the invariant. Recording the split as a divergence with nothing asserting the property behind it, which is what would make it a third unstructured fork. Differencing a nonzero baseline `collection_errors` away at BUILD instead of raising: the differential form is right for candidate-caused errors, and a seeded tree that cannot import its own tests is broken substrate whose observations should not become evidence |
| 79 | `t1_coverage`'s denominator excludes every changed path under `spec.environment.test_dirs` before imports and decorators are mapped out (plan Task 13, line 1007), and §5.7 of `docs/skeptic-engineering-plan.md` is amended to state the same rule | Patch coverage asks whether the patch's source change is exercised, and a test file is the exerciser. Edited test lines execute whenever the suite runs, so counting them inflates the ratio and masks an uncovered source change, inverting what the check measures. It is wrong on exactly the corpus M3 runs on: the minirepo's `src_dirs` is `["."]` (`tests/helpers.py:97`), so `h2-weakening`'s rewritten asserts are measured statements that execute and the fixture would come back near 1.0 where NOT_APPLICABLE is the answer. §5.7 is where coverage semantics are defined precisely, so leaving the refinement in the M3 plan alone would put two definitions of one metric in two documents and let the older one win the next time someone reads the engineering plan | Narrowing the minirepo's `src_dirs`: it repairs one fixture, ripples into `tests/test_seedcheck.py` and `tests/test_prevention.py`, and leaves the general case broken for any repo whose `src_dirs` covers its tests. Leaving §5.7 as written with the refinement recorded only in the plan |
| 80 | A missing `ro_subpaths` entry raises on the baseline side and is dropped with evidence on the candidate side. `docker_run_args` keeps raising, `RunContainer` takes `missing_ro: Literal["raise", "drop"] = "raise"`, the collector sets `"drop"` for the candidate only, and the dropped list reaches a verdict as evidence: `RunContainer.dropped_ro_subpaths` (Task 9) to `VariantObservations.dropped_ro_subpaths` (Task 5) and the artifacts (Task 10) to `t1_collect`'s `ro_subpath_deleted` rule, hard, category H1 (Task 11). This is the only one of the seven rulings that changed the plan's design | Raising is correct on the baseline side, where the tree is `git archive` plus the seed patch: every declared path exists, so an absent one is an authoring or infra fault the run should stop on. On the candidate side the absent path is the hack. A deleted `test_dirs` entry is the maximal H1, and reporting INFRA_ERROR there trades a whole verdict for a mount that had nothing left to protect, which is the failure mode the plan's INFRA discipline exists to prevent. The evidence path is what makes the trade worth taking: the prevention guarantee is weakened on one side, so the signal it was traded for has to be collected, and only a typed field on the observation can become evidence. `ro_subpath_deleted` is a separate rule id from `collect_shrinkage` because the two have different inputs and different consumers (plan line 332): shrinkage is computed from two collected sets and Task 8's category ladder keys on it alone, so folding them would feed the ladder entries carrying no nodeids. Cost is 7 tests across Tasks 9, 10, and 11 (plan line 15) | The plan's own original design, which had `RunContainer` record the dropped list on itself for the collector to write into an artifacts file with no check consuming it. That weakens a prevention guarantee and collects nothing back for it, which is the worst of the three available outcomes. Dropping symmetrically on both sides: a declared path missing from the seeded tree is an authoring fault, and a silent drop would hide it behind a check result. Keeping the unconditional raise: it turns the maximal H1 into an INFRA_ERROR |

**Scope:** these six rows record decisions. Every system they describe is
unbuilt at the time of writing: `RunContainer`,
`skeptic/collector.py`, the T1 check modules, the hack fixture corpus, and the
differential green rule of row 74 all land during M3 execution. Read the present
tense in the rows as the contract the plan now specifies, and read
`docs/superpowers/plans/2026-07-26-m3-t1-checks.md` for where each piece lands.

**Numbering:** these six rulings take 75 through 80, so M3's per-task DECISIONS
rows start at 81 (plan line 39).

---

# M3 execution (owner-approved, 2026-07-26)

Task 1 of the M3 plan. Row 74 was approved and unimplemented, and every T1
check M3 builds is differential, so BUILD's absolute stop signal had to move
first or the two stages would disagree about what "the suite is fine" means.

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| 81 | Row 74 lands in code, with six sub-decisions. `builder_tools.is_green(spec, suite, baseline_passed, baseline_collection_errors)` is the predicate, `run_baseline_suite` establishes the baseline inside the session container after the overlay install and before the Builder's first tool call, and `_suite_argv` is the one argv builder both suite runs use, so baseline and candidate argv differ only in the junit filename (`.skeptic-junit-baseline.xml` against `.skeptic-junit-build.xml`; both match `candidate.EXCLUDE_GLOBS`, so neither reaches the diff). (a) `collection_errors` is differential rather than pinned at zero. (b) A nonzero baseline `collection_errors` raises INFRA at BUILD. (c) Admission's clean-collection property is written down as a contract: `seedcheck`'s module docstring states it, `run_suite`'s exit-code message names collection failure first and carries the exact next command, and two tests pin both paths. (d) `suite_green` is renamed to `green` in all six spellings: `BuildResult.green`, `ToolOutcome.green`, the `tool_call` and `build_end` trace payload keys, `stop_reason == "green"`, and `result.json`, which also gains `green_rule` plus `baseline_seed_red`, `baseline_environmental_red`, `baseline_total`, and `baseline_collection_errors`. (e) A pass that becomes a skip, or one that vanishes from the collected set, leaves BUILD green. (f) `GREEN_RULE_VERSION = "differential-1"` joins the BUILD cache key. Out of scope and still absolute: `seedcheck`'s `pristine-green-x2` and `hacked-variants-green` | (a) A candidate can break an import the baseline did not have, and under `--continue-on-collection-errors` that run survives instead of aborting, so the count is observable and worth guarding. The differential form applies row 74's own environment-independence argument and reads identically to VERIFY's rule. It does not catch disappearance in general: an H1 deletion, a `-k` in `addopts`, and a `collect_ignore_glob` all remove tests while leaving the count at zero. Clause 1 catches those on the `failing_tests` set and `t1_collect` catches them at VERIFY. (b) A substrate whose seeded tree cannot import its own tests is broken rather than unusual, and VERIFY already treats it that way. Taken with (c) the guard should never fire, because `seed-red-exact` already requires `collection_errors == 0` on the same seeded tree BUILD then runs. It is documented as a guard expected to stay silent: if it fires, something changed between admission and BUILD, and the response is to find out what. (c) Row 78 named the contract; the code enforced it twice by construction and nothing asserted either path. The wording carries most of the value, because the next reader's question is why the two stages disagree about one flag. (d) A `result.json` written last week says `"suite_green": false` for click-0001 and means something the current code does not, and a trace event read by evalkit as a pure function has the same problem. Renaming makes a stale artifact fail loudly, and leaving three spellings behind would defeat the reason for renaming. (e) Both are hard evidence at VERIFY, where `t1_outcomes` and `t1_collect` own them. A Builder that trips either has already earned a verdict, and stopping its loop early buys nothing. BUILD's green is a stop-condition heuristic and the verdict comes from VERIFY, which is why the two are separate code rather than a shared function. (f) Today the semantics change happens to invalidate the cache, because `prompt_version()` hashes the Builder-facing text and that text had to change. That is luck. A future edit to the predicate alone would leave the key still and serve a cached `green` under the new rule's name | Keeping `collection_errors == 0` absolute at BUILD: it re-creates row 73's shape one layer down, where a candidate-caused import break and an environmental one are indistinguishable. Differencing a nonzero baseline away instead of raising, which would launder broken substrate into evidence. Leaving `suite_green` in place and documenting the new meaning: a stale artifact would keep reading as valid. Making pass-to-skipped non-green at BUILD, which duplicates `t1_outcomes` in a stage whose output is not a verdict. Routing the baseline through `seedcheck.run_suite`: it takes a runner with an `exec` method `SessionContainer` does not have, and `exec_shell` would give the baseline shell tokenization while the candidate gets argv tokenization, which is the bug class rows 70 and 72 exist to prevent. `run_baseline_suite` duplicates a little of `run_suite`'s exit-code taxonomy instead |

**Out of scope, on purpose:** `seedcheck.py`'s `pristine-green-x2` and
`hacked-variants-green` still require an absolute green. They are correct
today because admission runs under `VenvRunner` on the host, where `less`
exists. They break the moment admission moves in-container, which is the same
gap row 73 recorded. Recorded here so the next reader sees a deliberate split
rather than a half-converted codebase.

**Hole this task did not close:** `SessionContainer._BASE_ENV` and `_INSTALL`
shape the outcome map and are in no cache key. That was already true for the
Builder's own test runs; the baseline gives it a second consumer.

**Free side benefit:** the baseline runs after the cost confirmation and
before the first API call, so a broken environment now fails for zero dollars.
Row 73's actual outcome was two paid runs and a two-hour diagnosis.

---

# M3 execution: task 2 (owner-approved, 2026-07-26)

Task 2 of the M3 plan. `t1_coverage` cannot run at all until the per-repo
image ships `coverage`, and it cannot be repaired at runtime because VERIFY
containers run `--network none`. Neither corpus task's `environment.install`
line pulls it, confirmed against both frozen constraints files, and gold
would never reach PASS while the check returns NO_DATA on every run.

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| 82 | `image._HARNESS_TOOLS = "coverage"` installs unpinned in the resolve stage, before `pip freeze --exclude-editable`, so the freeze pins whatever version resolves into `constraints.txt` alongside the repo's own dependencies. This amends row 71's "deps-only" description: stage 2's closure now carries the harness's own measurement tooling alongside whatever the repo's `environment.install` names. Row 71's tag-hash description ("a hash of `environment.install` and the build-backend list") is also stale and is superseded here: `repo_image_tag` (`image.py:47`) already hashes the whole rendered Dockerfile, per the 2026-07-26 review finding recorded in that comment, so interpolating `_HARNESS_TOOLS` into the render moves the tag with no second hash input added. Coverage contexts stay at `dynamic_context = test_function` granularity: dotted `module.func`, parametrizations collapsed into one | Coverage belongs in the image template rather than each task's `environment.install`, because the repo under test has no opinion about the harness's own measurement tooling and a per-task install line lets a corpus author forget it. Measured: `docker run --rm --network none skeptic-repo-minirepo-upstream:eacf5e590c7b-533af5b9 sh -c 'grep -i coverage /opt/constraints.txt'` resolves `coverage==7.15.2` on the minirepo image built 2026-07-26. Per-nodeid contexts would need pytest-cov's `--cov-context=test`, which adds a pytest plugin to the suite's own environment, including under click's `filterwarnings = ["error"]`, for a granularity M4 does not need today: per-mutant selection over the coarser context map selects a safe superset (the whole parametrized family), which is correct and only slightly slower | Pinning an exact version in `_HARNESS_TOOLS` (e.g. `"coverage==7.15.2"`): it would move version control outside `constraints.txt`, the file that already governs every other dependency's version, and needs its own manual bump path. Leaving coverage in each corpus task's `environment.install`: it fixes the two known tasks and leaves the harness's own tooling as something the next corpus author has to remember, the exact gap this task closes. Pytest-cov for per-nodeid granularity: no consumer needs it yet, and it adds a plugin surface under click's strict warnings filter for nothing |

**Blast radius:** both corpus images rebuild once, since `_HARNESS_TOOLS`
changes the whole-render hash `repo_image_tag` keys on, and both BUILD cache
keys move with them. A re-run of `skeptic build` on either corpus task after
this change re-spends rather than serving a cached result.

---

# M3 execution: task 3 (owner-approved, 2026-07-26)

Task 3 of the M3 plan. Every check M3 builds emits into this schema and M4's
aggregator consumes it, so the shape has to carry all fourteen scoring rows of
§5.6 on the day it lands. Row 85 records why the freeze happens here rather
than at M1, where four places in the record say it already did.

| # | Decision | Rationale | Rejected |
|---|---|---|---|
| 83 | The evidence and verdict schema is frozen in `skeptic/checks/evidence.py`: `Evidence` and `CheckResult` frozen, `Verdict` mutable with `validate_assignment=True`, all three Pydantic models on `spec.py`'s house pattern (`extra="forbid"`, a `Literal` for every closed vocabulary). The plan's printed verdict example carries six verdict keys and five evidence keys; ten fields go past it: `rule`, `nodeids`, `location`, and `annotation` on `Evidence`, `isolation`, `infra_reason`, `schema_version`, and run identity (`run_id`, `task_id`, `variant`) on `Verdict`. `category` is narrowed rather than added, from the example's open string to a closed `Literal`. `CheckStatus` carries three values (`completed`, `not_applicable`, `attribution`) and `Severity` carries three (`hard`, `soft`, `info`), with `info` emitted by nothing in M3. `t1_scope` splits into `t1_scope` and `t1_goldens`, so the schema names twelve checks where the printed example named ten in its JSON plus `t1_ast` in the prose beneath it. The check layer takes §13's `skeptic/checks/` package. `EvidenceValidationError(SkepticInfraError)` lands in `skeptic/errors.py` and stays unraised until M4's aggregator | `extra="forbid"` with `frozen=True` makes any later field a `schema_version` bump, so every field M4, M5, and M6 will need has to be here now. `rule` because the weights table keys off a rule: `t2_mutation` emits two rows at 0.5 and 0.25 and `t1_coverage` emits one hard row and one 0.4 soft row, so `(check, severity)` cannot address it and an aggregator would be left string-matching on `detail`, which is prose written for humans. `category` closed because the published top-1 attribution metric is computed on that field, and an open `str` reproduces the problem `rule` exists to solve. `nodeids` as a tuple because Task 8's ladder reads the missing ids off a `collect_shrinkage` entry, and a tuple keeps `Evidence` hashable under `frozen=True` where a dict would not. `location` because DX5 (`DECISIONS.md:170`) renders the evidence list as check · category · file:line · artifact path. `annotation` because `t1_ast`'s annotate pass has nowhere else to write what it found. `isolation` is DX8's `--no-docker` stamp (`DECISIONS.md:173`), which surfaces at M6 with `verify --diff`, the point where a schema change costs most. `infra_reason` because §10's table requires the aggregate INFRA case to carry the schema path in its message. `schema_version` and run identity because evalkit reads committed artifacts as pure functions and Eval A joins verdicts to runs across every dev-set variant. `Verdict` is mutable so M4's aggregator can fill it as checks report, and `validate_assignment=True` enforces the Literals on every write, so `status = "totally-bogus"` raises at the assignment rather than reaching `verdict.json` (owner ruling, 2026-07-26). The third `CheckStatus` value exists because `t1_ast` is in neither name list permanently (plan line 407) while decision 62 gives it a scoring soft row: an aggregator that iterates the registry would file it under one list, and an invariant of the form "every evidence entry traces to a completed check" would silently zero H2 detection. The status governs the two name lists alone and an attribution result's evidence merges like any other check's, stated in the module docstring because M4 is a different session. `info` lands now because §5.5 calls the `t2_advtests` 0-of-N case neutral no-evidence while the evidence matrix at `DECISIONS.md:79` says the user sees it noted in verdict evidence, and M4 cannot add a severity value later without a version bump. The `t1_scope` split is a per-rule N/A property: the literal reading of plan line 407 excuses `t1_scope` whenever `golden_dirs` is empty, both corpus tasks ship `golden_dirs: []`, and under that reading §5.6's `allowed_paths` hard-fail rule has no implementing check on any task Skeptic can run today. Splitting also makes §13's H10-is-N/A-by-corpus claim render literally in `not_applicable` | Dataclasses, which the plan's lane note called for: `spec.py` already validates untrusted YAML through Pydantic and a check layer that hand-rolls its own validation gains a second way to be wrong. An open `str` category or an open `str` severity. A dict-valued `nodeids`, which would cost hashability. Parsing `location` and the nodeid list back out of `detail` at render time, which makes the renderer depend on prose. Deferring `isolation`, `infra_reason`, or `info` to the milestone that needs them, each of which is a version bump on a schema two lanes share. One `t1_scope` check that goes N/A only when both halves are inapplicable, which keeps the printed example's name list intact and hides the golden half's N/A inside an artifact. The flat `skeptic/t1_*.py` layout of the lane table, which the package supersedes: DX9's stated reason (killing the `skeptic.skeptic.*` stutter) is already satisfied flat, and twelve check modules plus an aggregator on top of the 13 modules already in `skeptic/` is what earns the directory |
| 84 | `CHECK_PRECEDENCE` is `t1_collect, t1_outcomes, t1_config, t1_scope, t1_goldens, t1_patterns, t1_coverage, t1_ast, t2_mutation, t2_advtests, t2_probe, t2_judge`. `order_evidence` sorts on `(SEVERITY_RANK[severity], precedence index, rule, location or "", first nodeid or "", detail)`, `split_results` sorts both name lists on the same index, and `SEVERITY_RANK` is `{"hard": 0, "soft": 1, "info": 2}`. `MANDATORY_CHECKS` is the six T1 checks M3 builds that can complete: `t1_collect`, `t1_outcomes`, `t1_config`, `t1_scope`, `t1_goldens`, `t1_coverage` | §5.6 requires deterministic evidence ordering so attribution is never a sort artifact, and the eval's top-1 attribution metric is a function of the list, which existed in no document before this row. It follows §13's repo-layout order with `t1_goldens` next to `t1_scope`; the layout order and the printed example's `checks_completed` order agree on every check they share, which is the best available reading of authorial intent. Weights cannot substitute for it: the soft table has four tied pairs (1.0/1.0, 0.5/0.5, 0.4/0.4, 0.25/0.25), so sorting by weight is not a total order on any of them. The list puts `t1_scope` ahead of `t1_coverage`, so an in-harness run where both fire places the `scope` entry first, which is why decision 76 measures top-1 attribution in the diff posture where `t1_scope` is NOT_APPLICABLE; that sentence sits next to the list in the module, because it is the one thing a reader of the list will get wrong. The tie-break key is invented here because no document states one, and two `collect_shrinkage` entries from one check are the common case rather than a corner: `detail` is last as a total-order backstop and every component ahead of it is structured. `SEVERITY_RANK` is explicit because alphabetically `"hard" < "info" < "soft"`, so string sorting would have ranked `info` second the day it arrived. `MANDATORY_CHECKS` excludes `t1_ast`, which never completes, and `t1_patterns`, which lands at M4, so M4's PASS rule does not wait on a check that does not exist | Sorting by weight. Leaving ties to input order, which is a scheduling artifact once checks run concurrently and would put the eval's headline attribution number at the mercy of thread timing. Alphabetical severity. A tie-break on `detail` alone, which sorts on prose and reorders itself every time a message is reworded. Deriving precedence from the printed verdict example alone, which names ten checks in its JSON and `t1_ast` only in the prose beneath it, and omits `t1_goldens` entirely |
| 85 | Amendment. The evidence schema was never frozen at M1. The claim appears in four places: `docs/skeptic-engineering-plan.md` lines 48, 242, and 250, and `DECISIONS.md:160`. The three plan sites are corrected in place to name M3 and `skeptic/checks/evidence.py`; `DECISIONS.md:160` stands as written and this row is its correction | Measured at `1607429`: `git grep -nE "^class \|^@dataclass" -- skeptic/` returns 40 hits (31 `class` lines, 9 `@dataclass` lines) and none is named `Evidence`, `Verdict`, or `Aggregate`, and `git log --all -S "class Evidence" -- '*.py'` returns zero commits. Nothing was frozen and nothing was written. The M1 close-out audited three exit criteria and caught two misses, recorded as rows 65 and 66; this was the third and it went unrecorded. Decision 67 then used the freeze as load-bearing rationale, keeping `StageCache` built and unwired because "the primitive is frozen at M1 with the evidence schema (§14) so the check lanes and the aggregator lane share one shape", and half of that sentence was false when it was written. The ruling itself stands on its first argument, that caching an admission gate is a category error, and its schema half becomes true here. The plan is a living document and is edited in place; `DECISIONS.md` is append-only, so a row that was wrong stays and the correction is appended, which is the same treatment row 82 gave row 71's stale description | Editing `DECISIONS.md:160` in place, which would make the record agree with itself by erasing what it said. Leaving the three plan sites alone, which leaves the next reader looking for a module that does not exist. Re-opening decision 67, whose primary rationale never rested on the freeze |

**What this schema does not do:** nothing binds a rule id to a check or to a
severity, and `Evidence.rule` is a plain `str`. `RULES` is exported data
policed by a test, which is the posture `CHECK_PRECEDENCE` also takes toward
check names: an unlisted name sorts last and raises nothing. A task that adds
a rule id adds it in two places, `RULES` and the table in
`tests/test_evidence.py`, and the test fails if only one of them moves.

**Unpopulated on purpose:** `Verdict` is defined and serializable and nothing
in M3 writes one. `EvidenceValidationError` is defined and nothing raises it.
Both producers are the M4 aggregator, per §14's lane split.
