import json, os, subprocess, time, shutil, pathlib
root = pathlib.Path(__file__).parent
work = pathlib.Path("/tmp/tipcalc")  # short path for display
shutil.rmtree(work, ignore_errors=True); work.mkdir()
env = dict(os.environ, PATH=f"{root/'stub_agents'}:{os.environ['PATH']}", GIT_AUTHOR_NAME="dev", GIT_AUTHOR_EMAIL="dev@example.com",
           GIT_COMMITTER_NAME="dev", GIT_COMMITTER_EMAIL="dev@example.com", PYTHONUNBUFFERED="1", COLUMNS="100")
for k in ("ALLOW_AUTO_MODE_CLAUDE", "ALLOW_AUTO_MODE_CODEX"): env.pop(k, None)
def quiet(c): subprocess.run(c, shell=True, cwd=work, env=env, capture_output=True, check=True)
quiet("git init -q -b main && echo '# tipcalc' > README.md && echo '__pycache__/\n.pytest_cache/' > .gitignore && git add . && git commit -qm 'initial commit'")
steps = []
def cmd(c, show=None, note=None, pause=1.2):
    t0 = time.time(); lines = []
    p = subprocess.Popen(c, shell=True, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for l in p.stdout: lines.append([round(time.time() - t0, 2), l.rstrip("\n")])
    p.wait()
    steps.append({"cmd": show or c, "out": lines, "rc": p.returncode, "note": note, "pause": pause})
    print(f"--- {show or c} (rc={p.returncode})"); [print(l[1]) for l in lines]
def setenv(k, v): env[k] = v
cmd('agent-loop init "Add a tip calculator with a CLI" --branch feature/tip-calculator', note="Describe the feature — a provider turns it into checkpoints")
cmd("agent-loop validate", pause=0.6)
cmd("agent-loop status", note="Two checkpoints, both pending")
cmd("agent-loop run", note="Guardrail: agents may only write files if you opt in", pause=1.8)
setenv("ALLOW_AUTO_MODE_CLAUDE", "1"); setenv("ALLOW_AUTO_MODE_CODEX", "1")
steps.append({"cmd": "export ALLOW_AUTO_MODE_CLAUDE=1 ALLOW_AUTO_MODE_CODEX=1", "out": [], "rc": 0, "note": None, "pause": 0.4})
cmd("git add plan_checkpoints.json && git commit -qm 'plan: tip calculator'", pause=0.6)
cmd("agent-loop run --audit-level redacted", note="Switches to the plan branch · claude builds · codex reviews", pause=2)
cmd("agent-loop status", note="Both checkpoints approved")
cmd("git log --oneline", note="Every build, revision and approval is a commit", pause=2.5)
cmd("grep -h RUN_OUTCOME logs/*.log | tail -1", show="grep RUN_OUTCOME logs/*.log", note="The run log is committed as an audit trail", pause=2.5)
json.dump(steps, open(root / "session.json", "w"), indent=1)
