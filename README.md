# Skeptic

Skeptic is a research harness for evaluating patches from coding agents. It
introduces known bugs into pinned versions of Click and Rich, then checks proposed
repairs for incomplete fixes and changes that make tests pass without fixing the
bug.

It also includes a report-only GitHub Action for auditing pull-request diffs in
supported Python repositories. Skeptic assumes supervised use on a host you
control. A `PASS` verdict does not prove that a patch is correct.

## Quick start

Requires Python 3.12.

```bash
git clone https://github.com/mamadou-wane/skeptic
cd skeptic
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]" -c requirements-dev.lock
skeptic demo
```

The bundled demo returns `PASS` for a correct fix and `FAIL` for a patch that
deletes the failing tests but leaves the bug. After installation, the demo runs
offline without Docker or an API key.

The `verify`, `build`, and `eval` commands require Docker. The paid profile and
any `build` run also require `ANTHROPIC_API_KEY`. Run `skeptic doctor` to check
your environment and see how to resolve missing requirements.

## How verification works

Skeptic runs candidate code on disposable copies in network-disabled Docker
containers. After each phase stops, the host validates its declared outputs and
stores them so later phases cannot change them. Checks inspect these artifacts
and, where needed, unchanged source trees without executing candidate code.

The default profile runs ten checks without API calls. The paid profile adds
LLM-generated adversarial tests and an LLM judge that reviews the diff. Reports
include evidence for individual checks and a verdict: `PASS`, `SUSPECT`, `FAIL`,
or `INFRA_ERROR`.

Candidate code can still influence the test and coverage data produced during its
own execution. See the [architecture guide](docs/architecture.md) for the
isolation limits and verdict rules.

## Evaluation

The evaluation uses constructed patches against Click and Rich. The following
comparison shows the recorded paid-profile headline results from September
2–3, 2026. “Flagged” means `SUSPECT` or `FAIL`.

| System | Development hacks flagged | Holdout hacks flagged |
| --- | --- | --- |
| Skeptic, paid profile | 27/29 | 10/11 |
| Test-suite-only baseline | 6/29 | 3/11 |
| Standalone LLM judge | 29/29 | 11/11 |

Skeptic returned `FAIL` for 12/29 development hacks and 5/11 holdout hacks. All
hard-rule failures in these sets were in categories the sandbox also prevents.
Skeptic flagged no patches in the three clean-control groups (0/12 in each); the
LLM judge flagged one patch in each of two groups.

These small datasets do not establish accuracy on ordinary pull requests.
Generated-test selection used the same clean controls, so those results are not
independent false-positive estimates for that mechanism. The holdout was authored
blind but later informed a scoring change; subsequent runs are not untouched
validation.

Some historical paid-run evidence is unavailable, including the original
generated tests, raw model responses, and detailed execution artifacts. The
recorded verdicts remain, but their sampled decisions cannot be fully inspected.
The [evaluation report](docs/evaluation.md) includes all baselines and repeat
runs; the [evidence policy](docs/evidence.md) describes retained records and
historical gaps.

## GitHub Action

The Action audits a pull-request diff and writes the results to the workflow
summary. It supports pytest-based Python repositories with a root
`pyproject.toml` or `setup.py` that pip can install. It runs the default profile
without API calls.

```yaml
on:
  pull_request: {}

jobs:
  skeptic:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: mamadou-wane/skeptic@78ce5da273ab6919f0e4d1fb298dca2359b0bb6f
        with:
          fail-on: never
```

The example pins a commit, not a release tag. Full Git history is needed to find
the merge base. See [the Action inputs](action.yml) for custom installation
commands and failure settings.

The Action is report-only by default because its false-positive rate on ordinary
clean pull requests is unmeasured. Unlike a seeded task, a diff audit has no
declared bug whose repair it can verify.

## Documentation and status

Skeptic is in maintenance.

- [Architecture](docs/architecture.md): execution isolation and verification rules.
- [Evaluation](docs/evaluation.md): datasets, comparisons, and recorded runs.
- [Evidence policy](docs/evidence.md): retained artifacts and inspection limits.
- [Admission reports](docs/admission/): supported upstream repositories and pinned commits.

## License

Skeptic is [MIT licensed](LICENSE).

Patch files and acceptance suites contain fragments of Click at `5aa8ac43527f`
(BSD-3-Clause, copyright 2014 Pallets) and Rich at `9d8f9a372cc5` (MIT, copyright
2020 Will McGugan), used to seed and verify bugs. Those fragments retain their
original copyrights and licenses.
