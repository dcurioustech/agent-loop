# Demo video

`agent-loop-demo.mp4` is a 64-second silent screencast of a **real** `agent-loop`
session in a throwaway `tipcalc` repo:

1. `agent-loop init` turns "Add a tip calculator with a CLI" into two checkpoints
2. `validate` / `status`
3. `run` refuses to start until `ALLOW_AUTO_MODE_*` is set
4. the loop switches to the plan branch; claude builds, codex reviews
5. the reviewer rejects phase1 (the CLI accepts a negative amount), the developer
   revises, the reviewer approves
6. `status`, `git log` (one commit per build / revision / approval) and the
   committed `RUN_OUTCOME` log line

Everything `agent-loop` prints is its real output, captured in `session.json`.
The **agents are scripted stand-ins** (`stub_agents/fakeagent.py`, installed on
PATH as `claude` and `codex`): they write real files and run real tests, but they
follow a script rather than calling a model. The side panel (checkpoint states,
git log) is derived from the captured output.

## Regenerating

```console
pip install -e . pytest pyflakes imageio-ffmpeg && npm i playwright
cp docs/video/stub_agents/fakeagent.py docs/video/stub_agents/claude
cp docs/video/stub_agents/fakeagent.py docs/video/stub_agents/codex
chmod +x docs/video/stub_agents/claude docs/video/stub_agents/codex
python3 docs/video/session.py          # runs the session in /tmp/tipcalc → session.json
python3 -c "import json;s=open('docs/video/player.html').read().replace('__SESSION__',open('docs/video/session.json').read());open('docs/video/player_built.html','w').write(s)"
node docs/video/render.js              # renders frames at 30 fps into docs/video/f/
"$(python3 -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')" -framerate 30 \
  -i docs/video/f/%05d.png -vf format=yuv420p -c:v libx264 -crf 18 docs/video/agent-loop-demo.mp4
```

Set `CHROME_PATH` if Playwright should use a specific Chromium binary.
