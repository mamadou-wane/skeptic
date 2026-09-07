"""Fixed Builder filesystem helper, executed with the image's isolated Python.

This module is stdlib-only: SessionContainer sends its source to Python -I -S.
No function here is called against a candidate tree by the host orchestrator.
"""
import base64
import json
import sys
from pathlib import Path

READ_MAX = 100_000
REPORT_MAX = 16 * 1024 * 1024
EXCLUDED = {".sv", ".pytest_cache", "__pycache__"}


def perform(root: Path | str, operation: str, arguments: dict) -> dict:
    root = Path(root).resolve()
    raw = str(arguments.get("path", ""))
    target = (root / raw).resolve()
    if target != root and root not in target.parents:
        return {"text": f"{raw!r} escapes the workspace.", "refused": True}
    rel = target.relative_to(root).as_posix()
    if operation == "list_files":
        if not target.is_dir():
            return {"text": "path must name a directory inside the workspace.", "refused": True}
        names = []
        for p in sorted(target.rglob("*")):
            parts = p.relative_to(root).parts
            if any(x in EXCLUDED or x.endswith(".egg-info") for x in parts):
                continue
            if p.is_file():
                names.append(p.relative_to(root).as_posix())
        text = "\n".join(names[:2000]) or "(empty)"
        if len(names) > 2000:
            text += "\n[truncated]"
        return {"text": text, "refused": False}
    if operation == "remove_report":
        # Only fixed report names are accepted by the host caller. Unlink the
        # entry itself, never the resolved target of a candidate-planted link.
        (root / raw).unlink(missing_ok=True)
        return {"text": "", "refused": False}
    if operation in ("read_file", "read_report"):
        if not target.is_file():
            return {"text": f"{raw!r} is not a file inside the workspace.", "refused": True}
        if operation == "read_report":
            with target.open("rb") as stream:
                data = stream.read(REPORT_MAX + 1)
            if len(data) > REPORT_MAX:
                return {"text": "JUnit report exceeds its size cap.", "refused": True}
            return {"text": base64.b64encode(data).decode("ascii"), "refused": False}
        cap = READ_MAX
        with target.open(encoding="utf-8", errors="replace") as stream:
            text = stream.read(cap + 1)
        if len(text) > cap:
            text = text[:cap] + "\n[truncated]"
        return {"text": text, "refused": False}
    if operation != "edit_file":
        return {"text": "Unknown filesystem operation.", "refused": True}
    allowed = arguments["allowed_paths"]
    if not any(rel == p.rstrip("/") or rel.startswith(p.rstrip("/") + "/") for p in allowed):
        return {"text": f"{rel!r} is outside allowed_paths {allowed}.", "refused": True}
    old, new = str(arguments["old_str"]), str(arguments["new_str"])
    if old == "":
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents replacing an entry that appeared after
        # validation. Docker's protected mounts enforce the write boundary.
        with target.open("x", encoding="utf-8") as stream:
            stream.write(new)
        return {"text": f"created {rel}", "refused": False}
    if not target.is_file():
        return {"text": f"{rel!r} does not exist; create it with an empty old_str.", "refused": True}
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        return {"text": f"old_str occurs {count} times in {rel}; it must occur exactly once.",
                "refused": True}
    target.write_text(text.replace(old, new, 1))
    return {"text": f"edited {rel}", "refused": False}


if __name__ == "__main__":
    try:
        result = perform("/workspace", sys.argv[1], json.loads(sys.argv[2]))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        result = {"text": f"{type(exc).__name__}: {exc}", "refused": True}
    print(json.dumps(result))
