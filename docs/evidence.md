# Decision evidence

Skeptic exports the records needed to inspect a decision after its disposable
execution trees are removed. An export does not prove a repair correct or
authenticate measurements produced during candidate execution. Skeptic is a
supervised research harness; exports assume a host the researcher controls.

## What a new export contains

`skeptic eval --out <directory>` exports each verification run. `build-arm`
exports each attempt after acceptance and classification. Direct `verify` and
`build` commands retain a source inventory in their run directories; export it
through `snapshot_run(run_directory, destination, exit_code)` using the command's
actual exit code. Use a fresh destination for each export.

Each new snapshot contains an `evidence/` directory. The snapshot's top-level
`meta.json` records the evidence format version and the SHA-256 of
`evidence/index.json`.

| Record | Retained content |
| --- | --- |
| Inputs | Admitted patch, effective task settings, seed/reference patch bytes, and dependency constraints where used |
| Provenance | Executed image identity and evaluator provenance |
| Checks | Reports, generated-test source and admission results, rejected tests, and negative observations |
| Execution | Admitted collection, JUnit, coverage, mutation, probe, and generated-test execution records needed to inspect the checks that ran |
| Model calls | Application-level requests and responses captured at the API boundary before interpretation; each attempt has paired request/response or error-type records |
| Outcomes | Verdict or arm classification, traces, and the originating stage trace for a replayed cache result |

Credentials and HTTP headers are not part of the model-call format. Builder
test reports retain the original received JUnit bytes and execution records.
Acceptance exports include held-out suite inputs and admitted reports. These
bundles are for researcher inspection; never mount one into a candidate
environment.

## Completeness and publication

Each verify, build, and acceptance run records an inventory of source-file
digests. Export streams the files, checks their
digests, and publishes them through the no-follow, regular-file, no-replace
artifact machinery. It publishes the index last. Existing exports are refused,
including failed or partial ones. Repair the underlying problem and export to a
new destination rather than overwriting a partial bundle.

`evidence/index.json` records logical and storage names, raw lengths and SHA-256
digests, stored-file digests, encoding, metadata, and omission policy. Missing
required sources, changed digests, or publication errors prevent a complete
export. Incomplete executions retain available diagnostics and remain marked
incomplete. A complete bundle means the declared evidence was retained, not that
the evaluated patch passed.

`evalkit.load_rows` and `load_arm_rows` validate new bundles before reading their
summaries. They reject missing indexes, changed evidence, partial exports, and
removed version markers. Legacy snapshots remain readable without gaining a new
completeness guarantee.

Digests detect changes relative to the index. They cannot protect against a host
owner who replaces both the evidence and its index.

## Large artifacts and disposable data

Coverage databases and coverage JSON use lossless gzip compression. Other
evidence files above 1 MiB are also compressed, except line-oriented traces
needed by existing readers. Copying and validation use bounded streaming chunks.
The admitted coverage limit is the per-file upper bound; a file that cannot be
retained within it causes an explicit export failure. No decisive measurement
is omitted merely because it is large.

Exports omit whole workdirs and disposable intermediates: pristine, seeded,
candidate, and mutant trees; venvs; containers; images; and caches. The index
records these omissions. Captured inputs and execution identities are retained
when execution reached those steps. There is no remote storage service or
automatic upload; the researcher controls export location and access.

Any future reduction of measurement data must establish that the retained subset
still supports decision inspection and record the original digest, size, and
omission reason. This format retains compressed originals instead.

## Historical paid runs

The ten historical paid sweeps retain their original committed verdicts, summaries,
traces, and manifests. Their original generated tests, raw model responses, and
detailed execution artifacts were not recovered from the available local
records. Those sampled decisions cannot currently be fully inspected.

The 380 committed pair snapshots remain unchanged. Their summaries and rescoring
can be reproduced from the retained records, but the new export policy does not
retroactively complete them. A new evaluation creates new evidence; it cannot
recover the original sampled artifacts.

## Scope of the public results

`PASS` means the configured mandatory checks completed or were explicitly not
applicable, the declared seeded outcomes passed, and the evidence stayed below
the rejection thresholds. Independent adverse evidence can still justify `FAIL`
or `SUSPECT` while another check is incomplete. Seedless diff audits establish no
seeded repair.

Generated-test admission uses the registered clean variants, making their results
conditioned controls for that mechanism. The holdout was authored blind but later
informed tuning. Neither result establishes a general probability of correct
repair. Retain all negative results and report each split with its own
denominator.

Timing and footprint figures apply to their measured revision and platform,
including the stated exclusions. The 2026-08-29 footprint describes `bc82e34`
and excludes base-image pull time; it is not a current-version runtime promise.
A maintenance release and its corresponding Action tag example require separate
review. An evidence-policy change does not create a release.
