# ShowMe

**Every product comes with an expert.** ShowMe reads a product's manual, watches you work through your phone's camera, and talks you through every step, catching mistakes before they matter. It runs on Meta's Muse Spark and Muse Wonder.

Built for the Meta Global AI Developer Hackathon (Oct 26 to Nov 4, 2026).

## One idea, two audiences

| | People assembling things | Employees learning machinery |
| --- | --- | --- |
| **How they start** | Scan the QR code on the box, or paste the Amazon / store link | Scan the QR code on the machine, or pick it from the list |
| **What ShowMe does** | Counts every part, guides each step in 3D, watches live, catches mistakes mid-way, picks up where you left off | Labels every control from one photo, answers "how do I…" from the manual, guides and checks each step |
| **Who pays** | The brand: fewer returns, support calls and bad reviews | The employer: faster onboarding and proof of training |
| **What the payer gets** | A dashboard: which step people get wrong, build times, the questions they ask | Training records: who is signed off on which machine, exportable as a spreadsheet |

The same engine serves both: the manual is read once, every step gets a visible "done when" condition, and the camera checks real work against it.

### Why not just upload the manual to ChatGPT?

A chatbot needs your hands, starts from zero every time, and can't check your work against the manual. ShowMe watches while your hands are busy, tracks which step you're on, warns you about the mistake earlier builders made at that step, counts your leftover screws, and gives the brand or employer a record of every verified step.

## Run it on Windows

1. Install Python 3.11 or newer from python.org (tick "Add python.exe to PATH").
2. Double-click `run.bat`. The first run creates `.venv`, installs packages, creates `.env`, and starts in **mock mode** (canned answers, no API calls).
3. Open `.env`, set `MODEL_API_KEY=` to your Meta Model API key, and run `run.bat` again. The header changes from "Mock mode" to "Live on Muse".
4. Your browser opens by itself once ShowMe answers, at `http://127.0.0.1:PORT`. If another app already answers on port 8000, ShowMe uses the next free port and prints the address in the window.

macOS or Linux: `./run.sh`.

### On your phone

- **Same Wi-Fi:** open the phone address printed in the `run.bat` window. Allow Python through Windows Firewall when asked. The camera works over plain http because photos are taken with the phone's own camera app.
- **Voice input** (the "Say it" button) needs https. Use a free tunnel, for example `cloudflared tunnel --url http://localhost:8000`, and open the https address it prints.

### Go to any step, or pick up where you left off

- **Any step, any order.** Tap any segment of the step bar, or open **All steps** on a step page, to jump anywhere. Each step shows whether it's checked, needs fixing, or hasn't been done. By voice: "go to step 4".
- **Where am I?** Already halfway through, or coming back later? Tap **Where am I?** (on any step, or **Already started? Find my place** before the parts check) and take one picture of the work. Muse Spark compares it with every step, checks off the steps that are visibly done, flags any finished step that was done wrong (that comes first, since later steps build on it), and takes you to the step to do now with the next one to three actions from exactly where you are. By voice: "where am I" or "what's next".

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

### QR guides, heads-ups, dashboards, parts ledger, hands-free

Because a chatbot starts from zero every time, needs your hands, and gives the brand nothing back. ShowMe adds:

- **QR guides: nothing to upload.** On **For brands: QR guides**, publish any manual that's been read. ShowMe builds every step's 3D scene in the background and gives you a QR code plus a printable box label. Scanning it opens `/p/CODE`, which goes straight to the product's guide. QR codes point at this PC's Wi-Fi address; set `PUBLIC_URL` in `.env` to an https tunnel or your domain so phones anywhere can open them.
- **Scan inside the app.** **Scan the code on the box** on the home screen reads a ShowMe QR code with the camera (the browser's own barcode reader, or the bundled jsQR, Apache 2.0). It works with any ShowMe link, so a code printed for this PC's Wi-Fi address also opens on the PC itself. On a phone over plain http, where live camera is blocked, take a photo of the code instead, or type the six-character code printed under it.
- **Heads-up from earlier builders.** Every caught mistake is logged per step. Before a step, Muse Spark turns the mistakes people made there into one spoken warning ("Most people fit panel B backwards here. The holes must face inward."), cached until a new mistake comes in. The live and photo checks are told to watch for that mistake specifically.
- **Brand dashboard.** For each guide: people started and finished, typical build time, mistakes caught, the share of people who got each step wrong, the hardest step with the heads-up given, and the questions owners asked (including ones the manual doesn't cover).
- **Parts ledger.** Each step shows how much loose hardware should be left afterwards. **Count leftover hardware** photographs what's left and compares: one screw too many means an earlier step is missing one, and the app names the step; too few means the steps ahead will run short.
- **Hands-free.** Turn on **Hands-free** (on by default from a QR code). The app reads the step and the heads-up, opens the camera by itself, coaches out loud, and moves to the next step when the check passes. Voice commands: "next", "skip", "back", "repeat", "check", "stop", "show me" (3D) and "parts". Voice commands need Chrome or Edge on this PC or over https; without them, everything else still works hands-free.

### Bought it online? Paste the store link

**Paste the Amazon or store link** on the home screen (or scan a store QR code) works with Amazon (including amzn.to short links), IKEA, Walmart, Flipkart, Best Buy, Target, Wayfair, eBay and most other shops:

1. ShowMe reads the link: the store, the product id (Amazon ASIN, IKEA article number...) and the name in the address.
2. It reads the product page: title, brand, model, feature bullets, product data, and any links to manuals.
3. **If the page links the official manual PDF**, ShowMe downloads it and reads it like an upload: the full guided, camera-checked setup.
4. **If not**, Muse Wonder writes a general setup guide from the listing, labeled "From the store listing, not the official manual" everywhere it appears. For anything that needs assembling it gives preparation steps only and asks for the printed instructions, because an invented assembly order is worse than none.

Many stores block automated page reads (Amazon's "Robot Check"). ShowMe detects that and still uses the product name in the link; if the link has no name, type the brand and model in the optional box. For safety it only opens normal public web pages (never this PC or your local network) and limits download sizes.

**No PDF at all?** Every manual upload also accepts photos: photograph each page of the paper booklet, in order, and upload them together.

### Machines and devices

Choose a manual under **Train on a machine** or **Learn a device** (or try the sample coffee maker) to open its page:

- **What does each button do?** One photo of the device; every control the manual names is labeled. Tap one to see it highlighted and hear what it does.
- **How do I…** Type, say, or tap a suggested question. Muse Wonder answers only from the manual, with page numbers, and says plainly when the manual doesn't cover it. When the answer is something to do, **Guide me through it** turns it into camera-checked steps. Each question is answered once and reused, and it shows up on the brand dashboard.
- **First-time setup** runs the manual's setup as a guided session.

### Teach a robot

The same engine that checks a person's work can teach a robot the same task. **For businesses → Robotics: teach a robot** (`#/robot`):

1. **Training data from real builds.** Every camera-checked step people did becomes a labelled example: the photo or live frame, the step's instruction (the language task), right or wrong, who decided (the camera or a person), and for mistakes, the correction that fixed them. **Download the robot dataset** gives a zip in a LeRobot-style layout: `meta/info.json`, `meta/tasks.jsonl`, `meta/episodes.jsonl`, `data/episode_NNNNNN.jsonl` and `images/`. No names or account details are included.
2. **The manual as robot steps.** Muse Wonder breaks each step into robot primitives (pick, place, insert, align, screw, tighten, press, rotate, hold, connect, inspect) with the part, where it goes, the tool and the detail that matters ("holes facing inward"). Each step keeps its visible "done when" as the success check, and steps a robot shouldn't do alone are flagged "Needs a person". Built once per manual and cached. If Muse is unavailable, a simple outline from the step text is shown and labelled.
3. **A success check for your robot.** After each step, a robot (or a simulator) sends one camera frame:

   ```
   curl http://localhost:8000/api/robot/check -F manual_id=man_... -F step=3 -F photo=@frame.jpg
   ```

   It gets back `success` (true or false), `reward` (1.0 or 0.0), what the camera saw, and the fix. It's the same Muse Spark check people get, so it can serve as a policy's reward signal or an evaluation harness.

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

## Host it for many people

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/thejakumar/ShowMe)

Fastest way to put it online: the button deploys this repo on Render's free plan (`render.yaml`). Paste your Meta Model API key when Render asks, and ShowMe is live at an `https://….onrender.com` address, so the live camera works on any phone and QR codes point there by themselves. The free plan sleeps after 15 minutes idle (the first visit then takes about a minute) and keeps no files between restarts, so it's for demos; add a Neon database for data that stays (see [DEPLOY.md](DEPLOY.md)).


On one PC, `run.bat` is all you need. To serve many shoppers, brands and employers at once, ShowMe runs as stateless web servers plus job workers on Postgres: `docker compose up -d --build` starts Postgres, the web app and two workers. With `DATABASE_URL` set, sign-in turns on:

- **Shoppers** never sign in. Their sessions belong to their phone.
- **Brands and employers** create a workspace at `#/account`. Their QR dashboards, machines and training records are visible only to members.
- **Employees** join at `#/join/CODE` with the workspace's join code and their name. No password.
- **Products are read once.** The same manual or store link (Amazon, IKEA...) is turned into a guide once and shared with everyone who asks for it.
- Slow model work runs in a job queue. Every call to Muse is rate-limited per person.

Architecture, settings and scaling steps are in [DEPLOY.md](DEPLOY.md).

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
  store.py     SQLite or Postgres + files in data/            |
  jobs.py      job queue: manuals, store links, 3D pre-builds |
  robot.py     robot steps, robot dataset, success check      |
        |                                                     |
Meta Model API: Muse Spark, Muse Wonder, SAM 3.1 -------------+
```

| Model | Job |
| --- | --- |
| **Muse Spark** | Reads every manual page (parts lists, numbered callouts, step diagrams). Finds and counts parts in photos using the manual's drawings as reference. Checks each step's after-photo against the manual's diagram and a "done when" condition. Lists objects in a room, and reads warning lights. |
| **Muse Wonder** | Turns the whole manual into a lesson plan: steps, visible done-when checks, why each step matters, warnings, and detector prompts per part. Writes concept lessons from the objects in your room, then fact-checks them in a second pass. Breaks each step into robot actions for **Teach a robot**. |
| **SAM 3.1** | Part finding and outlines, via the API or privately on your own GPU (hybrid mode). |

Rules the code enforces: every model reply is JSON validated against a schema (`schemas.py`), with one automatic repair attempt. Low-confidence answers ask the person. A person's confirmation is stored separately from the model's verdict.

## Privacy modes

The header switch decides where camera frames go.

- **Cloud:** photos go to Muse Spark for the best accuracy.
- **Hybrid:** frames stay on hardware you control. Part finding uses SAM 3.1 locally (`VISION_BACKEND=sam_local` on a PC with an NVIDIA GPU), or on your own GPU server (`sam_remote` + `SAM_REMOTE_URL`, running `backend/scripts/vision_server.py`). Manuals are still read once in the cloud. Step checks become a local parts check, which is less accurate, and the app says so. Lessons send Wonder only object names, never the photo.

The switch is disabled when no private backend is set up. SAM 3.1 setup is in `backend/apprentice/sam_local.py`.

## Proof it runs on Muse

- Every call is appended to `traces/YYYY-MM-DD.jsonl` with model, purpose, latency, token usage, the API's request ID, and a `cloud`, `local` or `mock` tag. Commit the traces from your demo runs.
- **Training records → Proof it runs on Muse** streams the calls live, which is good footage for the video.
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

## Also in the code (off the main path)

Two earlier experiments still work but are left out of the home screen and the demo to keep the product focused: **car checks** (`#/car`: engine-bay labels from the owner's manual, warning lights) and **learn any idea** (`#/learn`: a concept taught with the objects in your room). Session data also exports as plain JSON episodes (steps, photos, verdicts) from the training records page.

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
