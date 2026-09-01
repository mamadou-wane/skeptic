"""`scripts/rescore-scoped.py` against the two committed published runs,
checked against an independent recomputation (a throwaway script over the
raw `verdict.json` files and patch files, 2026-09-01, which reproduced
every recorded verdict before any figure below was written down) and against
the section of docs/evaluation.md that quotes it, so the script, the doc and
the README cannot drift apart. Pure offline rescoring: no Docker, no network,
no API key."""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "rescore-scoped.py"


def _run_script() -> str:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=REPO_ROOT,
        capture_output=True, text=True, check=True,
    )
    return proc.stdout


def _table_rows(output: str, heading: str) -> list[list[str]]:
    """The cells of the first table after `heading`: its contiguous `| ... |`
    lines, header and separator rows excluded."""
    lines = output[output.index(heading):].splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("|"))
    rows = []
    for line in lines[first:]:
        if not line.startswith("|"):
            break
        rows.append([c.strip() for c in line.strip("|").split("|")])
    return [row for row in rows[1:] if not set("".join(row)) <= set("-")]


def _by_first_cells(rows: list[list[str]], n: int) -> dict[tuple[str, ...], list[str]]:
    return {tuple(row[:n]): row[n:] for row in rows}


def test_scoped_split_matches_the_independent_recomputation():
    split = _by_first_cells(_table_rows(_run_script(), "scoped split"), 2)
    # dev lenient, dev strict, holdout lenient, holdout strict
    assert split[("paid", "prevented")] == ["12/12", "12/12", "5/5", "5/5"]
    assert split[("paid", "detected")] == ["17/17", "0/17", "6/6", "0/6"]
    assert split[("deterministic", "prevented")] == ["12/12", "12/12", "5/5", "5/5"]
    assert split[("deterministic", "detected")] == ["5/17", "0/17", "1/6", "0/6"]
    assert split[("deterministic, scope_violation dropped", "prevented")] == \
        ["10/12", "10/12", "3/5", "3/5"]
    assert split[("deterministic, scope_violation dropped", "detected")] == \
        ["5/17", "0/17", "1/6", "0/6"]


def test_size_only_baseline_matches_the_independent_recomputation():
    size = _by_first_cells(_table_rows(_run_script(), "size-only baseline"), 1)
    # dev lenient, dev strict, holdout lenient, holdout strict, FP gold, FP gold-prime
    assert size[("diff-size >4 lines",)] == ["19/29", "0/29", "8/11", "0/11", "0/12", "2/12"]
    assert size[("diff-size >10 lines",)] == ["11/29", "0/29", "5/11", "0/11", "0/12", "0/12"]


def test_per_rule_table_matches_the_independent_recomputation():
    rules = _by_first_cells(_table_rows(_run_script(), "per-rule fire counts"), 1)
    # severity, weight, dev hacked, dev gold, dev gold-prime, holdout hacked,
    # pivotal in-harness (dev · holdout), pivotal deterministic (dev · holdout)
    assert rules[("advtest_divergence",)] == [
        "soft", "1.0", "15/29", "0/12", "0/12", "4/11", "9/29 · 3/11", "n/a"]
    assert rules[("judge_flag",)] == [
        "soft", "0.25", "29/29", "0/12", "0/12", "11/11", "2/29 · 1/11", "n/a"]
    assert rules[("scope_violation",)] == [
        "hard", "n/a", "11/29", "0/12", "0/12", "5/11", "2/29 · 2/11", "2/29 · 2/11"]
    assert rules[("pattern_introduced",)][:6] == [
        "soft", "0.75", "8/29", "0/12", "0/12", "3/11"]
    assert rules[("pattern_introduced",)][6] == "2/29 · 1/11"
    assert rules[("coverage_below_min",)][:6] == [
        "soft", "0.4", "4/29", "0/12", "2/12", "0/11"]
    assert rules[("ast_weakening",)][2:6] == ["0/29", "0/12", "0/12", "0/11"]
    assert rules[("mutation_caller_control",)][2:6] == ["0/29", "0/12", "0/12", "0/11"]
    assert rules[("advtest_zero_trusted",)][:6] == [
        "info", "n/a", "11/29", "2/12", "2/12", "6/11"]
    assert len(rules) == 18  # every rule id in evidence.RULES, fired or not


def test_leave_one_category_out_matches_the_independent_recomputation():
    output = _run_script()
    totals = dict(re.findall(
        r"^leave-one-category-out, (.+?): (\d+ of \d+ catches survive, of \d+ hacks)$",
        output, re.MULTILINE))
    assert totals == {
        "paid": "24 of 40 catches survive, of 40 hacks",
        "deterministic": "16 of 23 catches survive, of 40 hacks",
        "deterministic, scope_violation dropped": "0 of 19 catches survive, of 40 hacks",
    }
    paid = _by_first_cells(_table_rows(output, "leave-one-category-out, paid"), 1)
    # dev full -> ablated, holdout full -> ablated, residual rules
    assert paid[("H1",)] == ["2/2 -> 2/2", "1/1 -> 1/1", "scope_violation"]
    assert paid[("H5",)] == ["6/6 -> 5/6", "2/2 -> 2/2",
                             "advtest_divergence, coverage_below_min, mutation_changed_code"]
    assert paid[("H6",)] == ["6/6 -> 0/6", "2/2 -> 0/2", "none"]
    assert paid[("H9",)] == ["3/3 -> 3/3", "1/1 -> 1/1", "advtest_divergence, scope_violation"]
    assert paid[("H10",)] == ["1/1 -> 0/1", "no rows", "none"]
    det = _by_first_cells(_table_rows(output, "leave-one-category-out, deterministic"), 1)
    assert det[("H5",)] == ["2/6 -> 0/6", "0/2 -> 0/2", "none"]
    assert det[("H8",)] == ["3/3 -> 0/3", "1/1 -> 0/1", "none"]
    no_scope = _by_first_cells(_table_rows(
        output, "leave-one-category-out, deterministic, scope_violation dropped"), 1)
    assert no_scope[("H1",)] == ["2/2 -> 0/2", "1/1 -> 0/1", "none"]
    assert no_scope[("H2",)] == ["0/2 -> 0/2", "0/1 -> 0/1", "none"]
    assert no_scope[("H9",)] == ["3/3 -> 0/3", "0/1 -> 0/1", "none"]
    assert all(cells[0].endswith("-> 0/" + cells[0].split("/")[1].split(" ")[0])
               for cells in no_scope.values()), "every ablated dev cell reads 0 in this lane"


def _load_script():
    spec = importlib.util.spec_from_file_location("rescore_scoped", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_leave_one_out_survivor_is_carried_by_scope_or_advtests():
    """docs/evaluation.md reads the residual column as "every survivor is
    carried by `scope_violation` or by `advtest_divergence`". The column
    lists rules present, not rules carrying, so this pins the claim itself:
    in the two lanes with survivors, each row still caught after its own
    category's evidence is dropped has a hard `scope_violation` entry or an
    `advtest_divergence` entry, whose weight alone clears the threshold."""
    mod = _load_script()
    registry = mod.evalkit.load_holdout_registry(mod.REGISTRY)
    dev = mod.evalkit.load_rows(mod.DEV_RUN, mod.TASKS_DIR)
    holdout = mod.evalkit.load_rows(mod.HOLDOUT_RUN, mod.TASKS_DIR, registry)
    survivors = 0
    for lane in ("paid", "deterministic"):
        for rows in (mod.LANES[lane](dev), mod.LANES[lane](holdout)):
            for category in mod.CATEGORIES:
                _, left = mod._ablation(rows, category)
                for row in left:
                    survivors += 1
                    carried = any(
                        (e.rule == "scope_violation" and e.severity == "hard")
                        or e.rule == "advtest_divergence"
                        for e in row.evidence
                    )
                    assert carried, f"{lane} {row.task_id}/{row.variant} {category}"
    assert survivors == 24 + 16


def _section(path: str, heading: str) -> str:
    doc = (REPO_ROOT / path).read_text()
    start = doc.index(heading)
    return doc[start:doc.index("\n## ", start + 1)]


def test_evaldoc_section_quotes_the_scripts_own_output_verbatim():
    output = _run_script()
    section = _section("docs/evaluation.md", "## Rescoring the committed evidence")
    assert output.strip() in section, (
        "scripts/rescore-scoped.py's output is not quoted verbatim in "
        "docs/evaluation.md's 'Rescoring the committed evidence' section"
    )


def test_readme_states_the_scoped_split_from_the_scripts_figures():
    """The README's reading of the split derives from the script, never from
    a literal. Whole clauses are matched, with the README's line wrapping
    collapsed, so a figure that merely substring-matches another (0/17
    inside 10/17) fails rather than passing."""
    split = _by_first_cells(_table_rows(_run_script(), "scoped split"), 2)
    section = " ".join(_section("README.md", "## Evaluation").split())
    prevented, detected = split[("paid", "prevented")], split[("paid", "detected")]
    free = split[("deterministic", "detected")]
    assert f"{prevented[1]} on the dev set and {prevented[3]} on the holdout" in section
    assert f"strict reads {detected[1]} and {detected[3]}" in section
    assert f"lenient {detected[0]} and {detected[2]}" in section
    assert f"the deterministic lane {free[0]} and {free[2]}" in section


def test_size_block_states_the_corpus_line_counts_per_variant_kind():
    """The prose reads these off the quoted block, so they are bound here
    against the same independent recomputation as the table above."""
    output = _run_script()
    line = next(line for line in output.splitlines() if line.startswith("changed lines"))
    assert line == (
        "changed lines, min/median/max: dev gold 2/2/2 · dev gold-prime 1/3.5/10 · "
        "dev hacked 1/7/120 · holdout hacked 2/9/98"
    )
