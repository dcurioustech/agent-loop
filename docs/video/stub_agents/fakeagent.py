#!/usr/bin/env python3
"""Scripted stand-in for a coding agent: does real file edits + real test runs."""
import json, re, subprocess, sys, time, pathlib
role = pathlib.Path(sys.argv[0]).name
prompt = sys.argv[-1] if role == "codex" else sys.argv[sys.argv.index("-p") + 1]
def say(s, d=0.25): print(s, flush=True); time.sleep(d)
st_path = pathlib.Path("plan_checkpoints.json")

if "Return ONLY" in prompt or "JSON" in prompt and not st_path.exists():
    print(json.dumps({"plan_file": "docs/implementation_plan.md", "branch": "feature/tip-calculator",
      "project": {"test_cmd": "python -m pytest -q", "lint_cmd": "python -m pyflakes tipcalc", "verify_in_review": True},
      "checkpoints": [
        {"id": "phase0", "name": "Core tip math", "status": "pending", "scope": "tipcalc/core.py with tip() and split()",
         "exit_criteria": ["tip(100, 15) == 15.0", "split() rounds each share up to the cent", "unit tests pass"], "attempts": 0, "review_notes": ""},
        {"id": "phase1", "name": "Command-line interface", "status": "pending", "scope": "python -m tipcalc AMOUNT --pct --people",
         "exit_criteria": ["CLI prints tip, total and per-person share", "rejects negative amounts with exit code 2"], "attempts": 0, "review_notes": ""}]}))
    sys.exit(0)

st = json.loads(st_path.read_text())
cid = re.search(r"checkpoint '(\w+)'", prompt).group(1)
cp = next(c for c in st["checkpoints"] if c["id"] == cid)
pkg = pathlib.Path("tipcalc"); pkg.mkdir(exist_ok=True); pathlib.Path("tests").mkdir(exist_ok=True)
(pkg / "__init__.py").touch()

def run(cmd):
    say(f"$ {cmd}", 0.1)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip().splitlines()
    for l in out[-3:]: say("  " + l, 0.12)
    return r.returncode

if role == "claude":
    if "did NOT approve" in prompt:
        say("Applying reviewer fixes for " + cid + "…")
        (pkg / "__main__.py").write_text(CLI_FIXED := open(pathlib.Path(__file__).with_name("cli_fixed.py")).read())
        say("  edited tipcalc/__main__.py: validate amount >= 0, exit 2")
        pathlib.Path("tests/test_cli.py").write_text(open(pathlib.Path(__file__).with_name("test_cli.py")).read())
        say("  added tests/test_cli.py::test_negative_amount")
        run("python -m pytest -q")
        cp["review_notes"] = "Added negative-amount guard (exit 2) + test."
    elif cid == "phase0":
        say("Reading plan: phase0 — Core tip math")
        (pkg / "core.py").write_text(open(pathlib.Path(__file__).with_name("core.py")).read())
        say("  wrote tipcalc/core.py  (tip, split)")
        pathlib.Path("tests/test_core.py").write_text(open(pathlib.Path(__file__).with_name("test_core.py")).read())
        say("  wrote tests/test_core.py (3 tests)")
        run("python -m pytest -q")
        cp["review_notes"] = "core.py: tip(), split() w/ ceil to cent; 3 tests."
    else:
        say("Reading plan: phase1 — Command-line interface")
        (pkg / "__main__.py").write_text(open(pathlib.Path(__file__).with_name("cli.py")).read())
        say("  wrote tipcalc/__main__.py (argparse CLI)")
        run("python -m tipcalc 84.50 --pct 18 --people 3")
        cp["review_notes"] = "CLI prints tip/total/per-person."
    cp["status"] = "built"
else:  # reviewer
    say(f"Reviewing {cid} — {cp['name']}")
    rc = run("python -m pytest -q")
    if cid == "phase1":
        rc2 = run("python -m tipcalc -- -5")
        if rc2 != 2:
            say("✗ exit criterion 2 not met: negative amount accepted (exit %d)" % rc2)
            cp["status"] = "built"
            cp["review_notes"] = "Criterion 2 fails: `python -m tipcalc -- -5` exits %d; must reject with exit code 2." % rc2
            st_path.write_text(json.dumps(st, indent=2) + "\n"); sys.exit(0)
    say("✓ all exit criteria met — approving " + cid)
    cp["status"] = "approved"
st_path.write_text(json.dumps(st, indent=2) + "\n")
