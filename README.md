# Apprentice

**Every product comes with an expert.** Upload a manual, point your phone at the real thing, and Apprentice finds each part, guides every step, and checks your work with Meta's Muse models.

Built for the Meta Global AI Developer Hackathon (Oct 26 to Nov 4, 2026).

| Use case | What happens |
| --- | --- |
| **Assemble something** (lead demo) | One photo of the loose parts: every piece is found, labeled with the manual's letter, and counted. Then each step is checked from a photo, and mistakes are caught with what's wrong, why it matters, and how to fix it. |
| **Train on a machine** | Photograph the machine and every control is labeled with what it does. Ask "how do I clear a jam?" and get walked through it from the manual, each step checked. Verified steps go on a skill passport. |
| **Learn a device** | The same for any electronic device: tap a button on the photo to hear what it does, ask "what does the orange light mean?", or "how do I descale it?" and follow the guided steps. |
| **QR guides for brands** | Publish a manual once and print the QR code on the box. Shoppers scan and start: no PDF, no upload, every 3D scene pre-built. Brands see where people struggle and what they ask. |
| **Car checks** | The engine bay is labeled from the owner's manual (dipstick, coolant, washer fluid) with page numbers, and dashboard warning lights are read. |
| **Learn any idea** | Ask about orbits or compound interest. The objects in your room become the lesson's cast, animated over your photo, and fact-checked first. |

Every session also exports as robot-ready episodes: steps, photos, and verdicts.

## Run it on Windows

1. Install Python 3.11 or newer from python.org (tick "Add python.exe to PATH").
2. Double-click `run.bat`. The first run creates `.venv`, installs packages, creates `.env`, and starts in **mock mode** (canned answers, no API calls).
3. Open `.env`, set `MODEL_API_KEY=` to your Meta Model API key, and run `run.bat` again. The header changes from "Mock mode" to "Live on Muse".
4. Your browser opens by itself once Apprentice answers, at `http://127.0.0.1:PORT`. If another app already answers on port 8000, Apprentice uses the next free port and prints the address in the window.

macOS or Linux: `./run.sh`.

### On your phone

- **Same Wi-Fi:** open the phone address printed in the `run.bat` window. Allow Python through Windows Firewall when asked. The camera works over plain http because photos are taken with the phone's own camera app.
- **Voice input** (the "Say it" button) needs https. Use a free tunnel, for example `cloudflared tunnel --url http://localhost:8000`, and open the https address it prints.

### Live camera check

**Check my work** opens a live camera view. About every two seconds a frame goes to Muse Spark, which replies with one short instruction for right now. The app shows it on the video and reads it aloud ("Turn panel B around so the holes face inward", then "Good, now fit the two screws"), outlining the problem area. It remembers its last few lines, so it can say "good, now…" as you fix things. When the step is clearly right (confidence at least `LIVE_PASS_CONFIDENCE`, default 0.75), it stops, keeps the passing frame on screen, and moves you on. The first mistake it sees and the final pass are saved to the passport with their frames.

**Start anywhere.** The camera judges where the work is right now, not where the manual starts: not started, in progress, in the middle of fixing a mistake, wrong, or done. It shows what's already right, a progress meter, and the next one to three actions from exactly that point ("Top screw's in. Now the bottom one."). Half-done and mid-fix frames count as progress, not mistakes; only a real error is logged as a caught mistake. If the camera sees that you're actually on a different step, it offers to jump there. Photo checks give the same "already done / next from here" breakdown.

Browsers allow live camera only on `localhost` or https. On the PC it works with the webcam. On a phone, use the https tunnel above; over plain Wi-Fi the button falls back to taking a photo.

### 3D and AR: see how to do the step

Each step's diagram has three tabs: **Manual**, **3D** and **AR**.

- **3D:** Muse Wonder reads the step's diagram and instructions and writes a small 3D scene: each part as a simple shape at real size (cm), where it waits, where it ends up, and a timeline of moves. Parts slide in, screws spin as they go in, tools turn, and a dashed yellow path shows where each part travels. One line per action is shown and read aloud. Drag to turn the model; pinch or scroll to zoom. Each step's scene is built once and cached in `data/manuals/<id>/anim/`. **Rebuild** asks Wonder again. If Wonder's answer can't be used, a simple preview plays instead and is labeled as such.
- **AR:**
  - On an ARCore Android phone in Chrome over https (WebXR), **Place it in your room** shows a ring on the floor or table, and a tap plays the step there at real size.
  - Anywhere else with a camera (the PC webcam, an iPhone), the animation plays over the live camera without being anchored, and the app says so.

3D rendering uses three.js (MIT licence), bundled in `backend/static/vendor/`, so no CDN is needed.

### Why not just upload the manual to a chatbot?

Because a chatbot starts from zero every time, needs your hands, and gives the brand nothing back. Apprentice adds:

- **QR guides: nothing to upload.** On **For brands: QR guides**, publish any manual that's been read. Apprentice builds every step's 3D scene in the background and gives you a QR code plus a printable box label. Scanning it opens `/p/CODE`, which goes straight to the product's guide. QR codes point at this PC's Wi-Fi address; set `PUBLIC_URL` in `.env` to an https tunnel or your domain so phones anywhere can open them.
- **Heads-up from earlier builders.** Every caught mistake is logged per step. Before a step, Muse Spark turns the mistakes people made there into one spoken warning ("Most people fit panel B backwards here. The holes must face inward."), cached until a new mistake comes in. The live and photo checks are told to watch for that mistake specifically.
- **Brand dashboard.** For each guide: people started and finished, typical build time, mistakes caught, the share of people who got each step wrong, the hardest step with the heads-up given, and the questions owners asked (including ones the manual doesn't cover).
- **Parts ledger.** Each step shows how much loose hardware should be left afterwards. **Count leftover hardware** photographs what's left and compares: one screw too many means an earlier step is missing one, and the app names the step; too few means the steps ahead will run short.
- **Hands-free.** Turn on **Hands-free** (on by default from a QR code). The app reads the step and the heads-up, opens the camera by itself, coaches out loud, and moves to the next step when the check passes. Voice commands: "next", "skip", "back", "repeat", "check", "stop", "show me" (3D) and "parts". Voice commands need Chrome or Edge on this PC or over https; without them, everything else still works hands-free.

### Machines and devices

Choose a manual under **Train on a machine** or **Learn a device** (or try the sample coffee maker) to open its page:

- **What does each button do?** One photo of the device; every control the manual names is labeled. Tap one to see it highlighted and hear what it does.
- **How do I…** Type, say, or tap a suggested question. Muse Wonder answers only from the manual, with page numbers, and says plainly when the manual doesn't cover it. When the answer is something to do, **Guide me through it** turns it into camera-checked steps. Each question is answered once and reused, and it shows up on the brand dashboard.
- **First-time setup** runs the manual's setup as a guided session.

## Settings (`.env`)

| Setting | Default | Meaning |
| --- | --- | --- |
| `MODEL_API_KEY` | (empty) | Meta Model API key. Empty means mock mode. |
| `MODEL_BASE_URL` | `https://api.meta.ai/v1` | API base URL. |
| `API_STYLE` | `auto` | `auto` tries chat completions, then switches to the Messages API if that endpoint is missing. Force with `openai` or `anthropic`. |
| `SPARK_MODEL` / `WONDER_MODEL` | `muse-spark-1.3` / `muse-wonder-1.0` | Model IDs. If the API rejects one ID (`model_not_found`), its jobs fall back to the other model automatically (shown in the trace). |
| `VISION_BACKEND` | `spark` | Where part finding runs: `spark`, `sam_api`, `sam_local`, or `sam_remote` (see Privacy). |
| `CHECK_EFFORT` / `PLAN_EFFORT` | `low` / `medium` | Reasoning effort. Dropped automatically if the API rejects it. |
| `JSON_MODE` | `false` | Ask for `json_object` replies. Off because Meta documents only `json_schema`; the prompts and one automatic repair already return valid JSON. |
| `MIN_CONFIDENCE` | `0.6` | Below this, the app asks the person instead of guessing. |
| `MAX_VISION_PAGES` | `24` | Long manuals (car manuals) send only the most relevant pages. |
| `APPRENTICE_MOCK` | auto | `1` forces mock mode even with a key. |
| `PUBLIC_URL` | (empty) | Address printed in QR codes, for example an https tunnel. Empty means this PC's Wi-Fi address. |

## How it works

```
Phone browser (camera, overlays, voice)
        |  photos, freeze frames
FastAPI backend ---------------------------------------------+
  ingest.py    PDF -> page images -> Spark reads each page    |  traces/*.jsonl
               -> Wonder writes the plan -> diagram crops     |  (every model call)
  vision.py    find parts: Spark boxes | SAM 3.1 (API/local)  |
  training.py  parts inventory, show a step's parts, check    |
  lessons.py   room objects -> lesson -> science review       |
  car.py       engine-bay labels, warning lights              |
  store.py     SQLite + files in data/                        |
        |                                                     |
Meta Model API: Muse Spark, Muse Wonder, SAM 3.1 -------------+
```

| Model | Job |
| --- | --- |
| **Muse Spark** | Reads every manual page (parts lists, numbered callouts, step diagrams). Finds and counts parts in photos using the manual's drawings as reference. Checks each step's after-photo against the manual's diagram and a "done when" condition. Lists objects in a room, and reads warning lights. |
| **Muse Wonder** | Turns the whole manual into a lesson plan: steps, visible done-when checks, why each step matters, warnings, and detector prompts per part. Writes concept lessons from the objects in your room, then fact-checks them in a second pass. |
| **SAM 3.1** | Part finding and outlines, via the API or privately on your own GPU (hybrid mode). |

Rules the code enforces: every model reply is JSON validated against a schema (`schemas.py`), with one automatic repair attempt. Low-confidence answers ask the person. A person's confirmation is stored separately from the model's verdict.

## Privacy modes

The header switch decides where camera frames go.

- **Cloud:** photos go to Muse Spark for the best accuracy.
- **Hybrid:** frames stay on hardware you control. Part finding uses SAM 3.1 locally (`VISION_BACKEND=sam_local` on a PC with an NVIDIA GPU), or on your own GPU server (`sam_remote` + `SAM_REMOTE_URL`, running `backend/scripts/vision_server.py`). Manuals are still read once in the cloud. Step checks become a local parts check, which is less accurate, and the app says so. Lessons send Wonder only object names, never the photo.

The switch is disabled when no private backend is set up. SAM 3.1 setup is in `backend/apprentice/sam_local.py`.

## Proof it runs on Muse

- Every call is appended to `traces/YYYY-MM-DD.jsonl` with model, purpose, latency, token usage, the API's request ID, and a `cloud`, `local` or `mock` tag. Commit the traces from your demo runs.
- **Team passport → Model trace** streams the calls live, which is good footage for the video.
- No API key or image data is ever written to the traces.

## Measure it

1. Copy `eval/cases.example.json` to `eval/cases.json`, and put your photos in `eval/photos/`.
2. Write the expected answers **before** running.
3. With the app running: `python eval/run.py --cases eval/cases.json`.

This writes `eval/results.md`: parts counted exactly, mistakes caught, false alarms, car parts found, and speed. Eval sessions are hidden from the passport.

## Check on day 1 with your key

The client follows the OpenAI-compatible chat format. These are assumptions to confirm against the Meta Model API docs:

- [ ] Model IDs (`SPARK_MODEL`, `WONDER_MODEL`) and that images are accepted as `image_url` data URLs.
- [ ] The maximum image size. `MAX_IMAGE_SIDE` is 1280 px.
- [ ] Whether `reasoning_effort` and `response_format` are accepted. If not, they are dropped automatically after the first rejection; check the trace for a "dropped unsupported parameter" line.
- [ ] For `VISION_BACKEND=sam_api`: the SAM 3.1 endpoint path and fields. Adjust `_sam_api_one()` in `vision.py`, the only place that knows them.

The quickest end-to-end test: open the app, choose **Try the sample step stool**, and watch the trace panel.

## Project layout

```
backend/apprentice/   API, model client, pipelines, prompts, schemas
backend/static/       phone web app (no build step)
backend/scripts/      vision_server.py (private SAM 3.1), make_sample_manual.py
backend/tests/        API flow tests (mock mode) and live-client tests (fake server)
eval/                 eval harness and case format
traces/               model-call logs
data/                 manuals, photos, database (created at run time, not committed)
```

Tests: `python -m pytest backend/tests`.

## Limits

- Overlays are drawn on still photos, not live video.
- Telling near-identical hardware apart (30 mm vs 35 mm screws) is hard for any vision model. Lay parts on the manual's 1:1 hardware drawing, or add a coin for scale. Low-confidence detections are dashed and counted as unsure.
- Car checks cover owner-level jobs only. Brakes, airbags, fuel and high-voltage parts are out of scope.
- The sample step stool is fictional and exists for testing.
