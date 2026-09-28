# Video summary

`agent-loop-summary.mp4` — a ~75-second, silent 1280×720 walkthrough of agent-loop:
the developer/reviewer idea, the `plan_checkpoints.json` state file, the per-checkpoint
loop, providers, guardrails, the audit trail, `agent-loop init`, and a quickstart.

## Regenerating

The video is rendered from `agent-loop-summary.html` (a self-playing HTML animation;
open it in a browser to preview it).

```console
npm i playwright && pip install imageio-ffmpeg
node record.js    # writes frames/ and frames/list.txt
cd frames && "$(python3 -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')" \
  -f concat -safe 0 -i list.txt -vf "fps=30,format=yuv420p" -c:v libx264 -crf 20 \
  -movflags +faststart ../agent-loop-summary.mp4
```

Set `CHROME_PATH` if Playwright should use a specific Chromium binary.
