# Decision evidence

Skeptic is a supervised research and evaluation harness. Its exports preserve
records needed to inspect a decision after disposable execution trees are
removed. They do not prove a repair correct or authenticate measurements that
candidate code produced during its own phase.

## What a new export contains

`skeptic eval --out <directory>` exports each verification run. `build-arm`
exports each attempt after acceptance and classification. Direct `verify` and
`build` retain a source inventory in their run directory; the existing Python
`snapshot_run(run_directory, destination, exit_code)` API can export it using
the command's actual exit code. Use a fresh destination for each export.

Each new snapshot has an `evidence/` directory and `meta.json` identifies its
format version and index digest. The bundle contains:

- The admitted patch and captured task inputs, including seed/reference patch
  bytes and dependency constraints where used, plus effective task settings.
- The executed image identity and evaluator provenance.
- Check reports, generated-test source and its admission results, including
  rejected tests and negative observations.
- Admitted collection, JUnit, coverage, mutation, probe and generated-test
  execution records needed to inspect the checks that ran.
- Actual application-level model requests and responses, captured at the API
  boundary before interpretation. Each attempt has paired request/response or
  error-type records. Credentials and HTTP headers are not part of this format.
- Verdict or arm classification, traces, and the originating stage trace when
  a cache result is replayed.

Builder test reports contain the original received JUnit bytes and execution
records. Acceptance exports include the held-out suite inputs and admitted
reports. These files are evidence for the researcher; never mount an exported
bundle into a candidate environment.

## Completeness and publication

A stage records an inventory of source-file digests. Export streams those files,
checks their digests, and publishes files through the existing no-follow,
regular-file and no-replace artifact machinery. The index is published last.
An existing export is refused; a failed or partial export is not overwritten
into apparent success. Re-export to a fresh destination after repairing the
underlying problem.

`evidence/index.json` records logical names, storage names, raw lengths and
SHA-256 digests, stored-file digests, encoding, metadata and omission policy.
A complete bundle means the declared decision evidence was retained. It does
not mean the evaluation passed. Incomplete executions retain available
diagnostics and remain explicitly incomplete. A missing required source,
changed digest or publication error prevents a complete export.

`evalkit.load_rows` and `load_arm_rows` validate new bundles before consuming
their summaries. They reject a missing index, changed evidence, partial export
or removed version marker. Legacy snapshots remain readable; they acquire no
new completeness guarantee. Digests detect damage or replacement relative to
the index, not a malicious host owner who replaces both records and index.

## Large artifacts and disposable data

Keep decisive measurements. Coverage databases and coverage JSON are retained
with lossless gzip compression; other evidence files above 1 MiB are compressed
as well, except line-oriented traces needed by existing readers. Copying and
validation stream bounded chunks rather than loading large measurements into
memory. The existing admitted coverage limit remains the upper bound; a file
that cannot be retained within the contract causes an explicit export failure.
No decisive measurement is omitted merely because it is large.

Do not retain whole workdirs. Pristine, seeded, candidate and mutant trees,
venvs, containers, images and caches are omitted as disposable intermediates.
The index states these omissions; captured inputs and recorded identities are
retained when execution reached those steps. There is no remote storage service
or automatic upload. Export location and access remain under the researcher.

A future reduction of measurement data must establish that the retained subset
still supports inspection of the decision, then record the original digest,
size and omission reason. This version keeps compressed originals instead of
introducing a projection that could discard decisive context.

## Historical paid runs

The ten paid sweeps retain their original committed verdicts, summaries, traces,
and manifests. The original generated tests, raw model responses, and detailed
execution artifacts were not recovered from the available local records. Their
sampled decisions cannot presently be fully inspected. A new evaluation would
produce new evidence and would not recover those historical artifacts.

The 380 committed pair snapshots remain unchanged. Summaries and rescoring can
be reproduced from those records; full inspection of the original sampled
decisions cannot currently be promised. New export policy is prospective.

## Scope of the public results

PASS means the configured mandatory checks completed or were explicitly not
applicable, the declared seeded outcomes passed, and the evidence did not reach
a rejection threshold. An independent adverse finding can still justify FAIL
or SUSPECT while another check is incomplete. Seedless diff audits do not
establish a seeded repair.

Registered clean variants are used by generated-test admission, so their
results are conditioned controls for that mechanism. The holdout was authored
blind but later informed tuning. Neither establishes a general probability of
correct repair. Keep all negative results and separate denominators.

Published timing and footprint figures describe their measured revision,
platform and exclusions. The 2026-08-29 footprint is commit `bc82e34`, with base
image pull time excluded. It is not a current-version runtime promise. A future
maintenance release and the corresponding Action tag example require separate
release review; no release is created by this evidence change.
