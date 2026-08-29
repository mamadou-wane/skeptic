"""Time a stranger's path from clone to first verdict, and size what it leaves.

    python scripts/footprint.py --repo <url> --ref <commit> --out <json> [--prune-build-cache]

One wall-clock run of the README's getting-started steps, in a fresh temp
dir, each timed with `time.monotonic` around a subprocess:

  clone    `git clone <repo>` plus `git checkout <ref>`
  install  `python3.12 -m venv .venv` then `pip install -e ".[dev]"
           -c requirements-dev.lock`, pip's cache off, so nothing this machine
           downloaded before counts for it; the index and any pip.conf still
           come from the host and are recorded
  demo     `skeptic demo`, the keyless first verdict
  doctor   `skeptic doctor`, exit code recorded; a stranger has no API key
  cold     `skeptic verify --task click-0001 --variant gold`, deterministic
           profile, with the task's image tag removed first: pinned-commit
           clone, image build under the pin, first real verdict. The tag is
           the one this host's own corpus image carries, so the host rebuilds
           it too; whether the removal happened is recorded
  warm     the same command again, which replays the stage cache

Removing the tag does not empty Docker's build cache, and a warm cache
replays the whole resolve stage, installs included. `--prune-build-cache`
runs `docker builder prune -af` before the cold step so the build pays what
a stranger pays, and the record says whether it ran. It is opt-in because it
empties the host's entire build cache.

The base image pull is not timed. The machine that measures already holds
the image, and Docker will not remove an image the corpus images sit on.
What is recorded instead is what a stranger downloads: the sum of the layer
sizes the registry manifest lists for this machine's platform.

Sizes after the run: bytes of files for the checkout, the venv and the
workdir; image content size (what `docker image inspect` reports, which on
the containerd store is the compressed layer set, base layers included) for
the base image and the task image. Everything goes to one JSON record; the
markdown rows docs/evaluation.md carries are printed so the drift test can be
read against the same source. The total is the sum of the timed clone,
install and cold steps, not one elapsed clock.

Not a benchmark: one run on one machine, and the record says which. macOS or
Linux with a Docker daemon the current user can drive.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

TASK = "click-0001"
VARIANT = "gold"


def sh(argv: list[str], cwd: Path, env: dict[str, str] | None = None,
       timeout_s: int = 1800) -> tuple[float, subprocess.CompletedProcess]:
    started = time.monotonic()
    proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=timeout_s, check=False)
    return time.monotonic() - started, proc


def du(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def image_size(ref: str) -> int | None:
    proc = subprocess.run(["docker", "image", "inspect", "--format", "{{.Size}}", ref],
                          capture_output=True, text=True, check=False)
    return int(proc.stdout.strip()) if proc.returncode == 0 else None


def download_size(ref: str, arch: str) -> int | None:
    """Bytes a pull of `ref` transfers for `arch`: the layer sizes the
    registry's manifest lists for that platform, read without pulling."""
    proc = subprocess.run(["docker", "manifest", "inspect", "-v", ref],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None
    for entry in json.loads(proc.stdout):
        plat = entry.get("Descriptor", {}).get("platform", {})
        if plat.get("os") == "linux" and plat.get("architecture") == arch:
            manifest = entry.get("SchemaV2Manifest") or entry.get("OCIManifest") or {}
            return sum(layer["size"] for layer in manifest.get("layers", []))
    return None


def machine() -> dict:
    info = {
        "os": f"{platform.system()} {platform.mac_ver()[0] or platform.release()}",
        "arch": platform.machine(),
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
    }
    if platform.system() == "Darwin":
        for key, name in (("machdep.cpu.brand_string", "cpu"), ("hw.memsize", "memory_bytes")):
            out = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True,
                                 check=False).stdout.strip()
            info[name] = int(out) if name == "memory_bytes" else out
    docker = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                            capture_output=True, text=True, check=False)
    info["docker"] = docker.stdout.strip() or None
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--repo", required=True, help="clone URL, or a local path for a dry run")
    ap.add_argument("--ref", required=True, help="commit to measure")
    ap.add_argument("--out", type=Path, required=True, help="JSON record to write")
    ap.add_argument("--python", default="python3.12")
    ap.add_argument("--prune-build-cache", action="store_true",
                    help="run `docker builder prune -af` before the cold verify")
    args = ap.parse_args()

    python = shutil.which(args.python)
    if python is None or shutil.which("docker") is None or shutil.which("git") is None:
        print("needs python3.12, docker and git on PATH", file=sys.stderr)
        return 3

    record: dict = {
        "schema_version": 1,
        "measured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": args.repo,
        "ref": None,
        "task": TASK,
        "variant": VARIANT,
        "machine": machine(),
        "steps": {},
        "sizes_bytes": {},
    }
    record["machine"]["pip_config"] = subprocess.run(
        [python, "-m", "pip", "config", "list"], capture_output=True, text=True,
        check=False).stdout.strip()
    steps = record["steps"]

    with tempfile.TemporaryDirectory(prefix="skeptic-footprint-") as tmp:
        root = Path(tmp)
        clone = root / "skeptic"

        secs, proc = sh(["git", "clone", "--quiet", args.repo, str(clone)], cwd=root)
        if proc.returncode != 0:
            print(proc.stderr, file=sys.stderr)
            return 3
        t_checkout, co = sh(["git", "checkout", "--quiet", args.ref], cwd=clone)
        if co.returncode != 0:
            print(co.stderr, file=sys.stderr)
            return 3
        record["ref"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=clone, check=False,
                                       capture_output=True, text=True).stdout.strip()
        steps["clone"] = {"seconds": round(secs + t_checkout, 1)}

        venv = clone / ".venv"
        env = {**os.environ, "PIP_NO_CACHE_DIR": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
        env.pop("ANTHROPIC_API_KEY", None)
        t_venv, proc = sh([python, "-m", "venv", str(venv)], cwd=clone)
        if proc.returncode != 0:
            print(proc.stderr[-2000:], file=sys.stderr)
            return 3
        pip = str(venv / "bin" / "pip")
        t_pip, proc = sh([pip, "install", "-q", "-e", ".[dev]", "-c", "requirements-dev.lock"],
                         cwd=clone, env=env)
        if proc.returncode != 0:
            print(proc.stderr[-2000:], file=sys.stderr)
            return 3
        steps["install"] = {"seconds": round(t_venv + t_pip, 1), "pip_cache": "off"}
        skeptic = str(venv / "bin" / "skeptic")

        secs, proc = sh([skeptic, "demo"], cwd=clone, env=env)
        steps["demo"] = {"seconds": round(secs, 1), "exit": proc.returncode,
                         "verdicts": [line for line in proc.stdout.splitlines()
                                      if line.startswith("VERDICT")]}

        secs, proc = sh([skeptic, "doctor"], cwd=clone, env=env)
        steps["doctor"] = {"seconds": round(secs, 1), "exit": proc.returncode,
                           "api_key": "absent"}

        # The base image the task image builds on: sized, not pulled (see the
        # module docstring for why the pull is not timed).
        base = subprocess.run(
            [str(venv / "bin" / "python"), "-c", "from skeptic.image import BASE_IMAGE; print(BASE_IMAGE)"],
            cwd=clone, capture_output=True, text=True, check=False).stdout.strip()
        arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine(), platform.machine())
        record["base_image"] = {"ref": base, "platform": f"linux/{arch}",
                                "download_bytes": download_size(base, arch),
                                "pull_timed": False}

        tag_expr = ("from pathlib import Path; from skeptic.spec import find_task; "
                    "from skeptic.image import repo_image_tag; "
                    f"print(repo_image_tag(find_task({TASK!r}, Path('tasks'))))")
        tag = subprocess.run([str(venv / "bin" / "python"), "-c", tag_expr],
                             cwd=clone, capture_output=True, text=True, check=False).stdout.strip()
        removed = subprocess.run(["docker", "image", "rm", tag], capture_output=True, check=False)
        record["task_image_tag"] = tag
        record["task_image_removed"] = removed.returncode == 0
        record["build_cache"] = "as found"
        if args.prune_build_cache:
            pruned = subprocess.run(["docker", "builder", "prune", "-af"],
                                    capture_output=True, text=True, check=False)
            record["build_cache"] = "pruned" if pruned.returncode == 0 else "prune failed"
        verify = [skeptic, "verify", "--task", TASK, "--variant", VARIANT]
        for name in ("cold", "warm"):
            secs, proc = sh(verify, cwd=clone, env=env)
            verdict = next((line for line in proc.stdout.splitlines() if line.startswith("VERDICT")), None)
            steps[name] = {"seconds": round(secs, 1), "exit": proc.returncode, "verdict": verdict}
            if verdict is None:
                print(proc.stdout[-1500:], proc.stderr[-1500:], file=sys.stderr)
                return 3

        sizes = record["sizes_bytes"]
        sizes["checkout"] = du(clone) - du(venv) - du(clone / "workdir")
        sizes["venv"] = du(venv)
        sizes["workdir"] = du(clone / "workdir")
        sizes["base_image"] = image_size(base)
        sizes["task_image"] = image_size(tag)

    record["totals"] = {
        "clone_to_demo_s": round(sum(steps[s]["seconds"] for s in ("clone", "install", "demo")), 1),
        "clone_to_first_verdict_s": round(
            sum(steps[s]["seconds"] for s in ("clone", "install", "cold")), 1),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(render(record))
    return 0


def render(record: dict) -> str:
    """The rows docs/evaluation.md carries, from the record alone."""
    s = record["steps"]
    sizes = dict(record["sizes_bytes"])
    sizes["download"] = record["base_image"]["download_bytes"]
    mb = {k: (f"{v / 1e6:.0f} MB" if v is not None else "n/a") for k, v in sizes.items()}
    cache = "build cache pruned" if record.get("build_cache") == "pruned" else "build cache as found"
    rows = [
        "| step | wall-clock | what it leaves |",
        "|---|---|---|",
        f"| clone and checkout | {s['clone']['seconds']:.0f} s | checkout {mb['checkout']} of files |",
        f"| venv and install, pip cache off | {s['install']['seconds']:.0f} s | venv {mb['venv']} of files |",
        f"| `skeptic demo` | {s['demo']['seconds']:.1f} s | {len(s['demo']['verdicts'])} verdicts, no Docker, no key |",
        f"| base image pull | not timed | {mb['download']} to download, {mb['base_image']} of image content |",
        f"| first `verify`, {cache} | {s['cold']['seconds']:.0f} s | task image {mb['task_image']} of content, base layers included; workdir {mb['workdir']} of files |",
        f"| second `verify`, warm | {s['warm']['seconds']:.1f} s | stage cache replay |",
    ]
    t = record["totals"]
    rows.append(f"| clone, install and first verify summed, pull excluded | {t['clone_to_first_verdict_s']:.0f} s | |")
    return "\n".join(rows)


if __name__ == "__main__":
    sys.exit(main())
