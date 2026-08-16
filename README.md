# History Shorts Pipeline

Fully automated "did you know" history Shorts, built entirely on free-tier tools and
GitHub Actions. Uploads **2 videos per day**, each with its own auto-generated title,
description, and tags — no manual step required. Every beat of narration is matched to
a footage clip and caption of the *same duration*, so the visuals cut exactly when the
story moves to its next fact.

## How it all fits together

1. `generate_script.py` asks an LLM (Groq) for **one JSON object** containing the
   script beats *and* the video's title/description/tags — one call produces
   everything needed to both build and publish the video.
2. `generate_narration.py` synthesizes each beat's audio **separately** with edge-tts
   and measures its exact duration.
3. `fetch_footage.py` finds a public-domain clip on the Internet Archive for each
   beat's footage query.
4. `assemble_video.py` trims (or loops) each footage clip to *exactly* match its
   beat's narration duration, mixes in the audio, concatenates all beats, and burns
   in captions timed to the same beat boundaries.
5. `upload_youtube.py` publishes the result as a Short using the auto-generated
   title/description/tags (plus an appended public-domain source credit line).
6. `topic_queue.py` picks the next unused topic from `config/topics.json` and
   advances a pointer in `config/state.json`, which the workflow commits back to the
   repo — so the two runs each day (and every day after) always get a fresh topic
   until the whole pool cycles.

Because each beat is generated and trimmed independently, footage/narration/captions
stay locked together — that's the sync trick. Because metadata comes from the same
LLM call as the script, titles/descriptions/tags are always specific to that video's
actual facts, not a generic template.

## Repo layout

```
src/
  generate_script.py     Groq call -> {title, description, tags, beats}
  fetch_footage.py        Internet Archive search + download (public domain only)
  generate_narration.py   edge-tts per-beat audio
  assemble_video.py       ffmpeg trim/mux/concat/caption
  upload_youtube.py       YouTube Data API v3 upload
  topic_queue.py          non-repeating topic picker + state pointer
  pipeline.py             orchestrates all of the above
  utils.py                logging / ffprobe helpers
config/
  topics.json             pool of history topics (40 to start — add more any time)
  state.json              {"next_index": N} — auto-updated by the workflow, don't hand-edit
.github/workflows/
  pipeline.yml            two scheduled runs per day
```

## Setup

### 1. Install locally (optional, for testing)

```bash
pip install -r requirements.txt
sudo apt install ffmpeg   # or brew install ffmpeg on macOS
cp .env.example .env      # fill in GROQ_API_KEY at minimum
```

Test a dry run (builds the video, skips upload):

```bash
cd src
export $(grep -v '^#' ../.env | xargs)
DRY_RUN=1 python pipeline.py "the Apollo 11 moon landing"
```

Output lands at `output_preview.mp4` in the repo root. Print a script + metadata
without building anything:

```bash
python generate_script.py "the sinking of the Titanic"
```

### 2. APIs you need to add

| Secret | Where to get it | Cost |
|---|---|---|
| `GROQ_API_KEY` | console.groq.com → API Keys | Free tier |
| `YT_CLIENT_ID` / `YT_CLIENT_SECRET` | Google Cloud Console → enable **YouTube Data API v3** → Credentials → OAuth client ID (type: Desktop app) | Free (quota-limited) |
| `YT_REFRESH_TOKEN` | One-time OAuth flow using the client ID/secret above with scope `youtube.upload` — Google's OAuth Playground works for this one-time step | Free |

Archive.org and edge-tts need **no API key** — both are open/free by design.

### 3. Add secrets to GitHub

Repo → Settings → Secrets and variables → Actions → New repository secret, for each
of the four keys above.

### 4. Allow the workflow to push commits

The workflow commits the advanced topic-queue pointer back to the repo after each
successful run, so it needs write access:
- Repo → Settings → Actions → General → Workflow permissions → **Read and write
  permissions**.
- (The `permissions: contents: write` block already in `pipeline.yml` covers the
  per-workflow grant; the repo-level setting above just needs to allow it.)

### 5. Enable the schedule

`pipeline.yml` runs at 13:00 UTC and 01:00 UTC — roughly 12 hours apart to reach
different audience timezones. Adjust the two `cron` lines to whatever times you
prefer. You can also trigger a run manually from the Actions tab, with an optional
forced topic and a dry-run checkbox that skips upload (and skips the state commit).

## Notes / things worth tuning

- `SAFE_COLLECTIONS` in `fetch_footage.py` is a starting list of Archive.org
  collections that are reliably public domain — expand it as you find good sources.
- If a beat's footage search comes up empty, the pipeline fails loudly rather than
  silently falling back to filler — a failed run does **not** advance the topic
  queue, so the next scheduled run retries the same topic. If failures are frequent,
  consider adding a small pool of generic "safe" clips per era as a last resort.
- `DEFAULT_VOICE` in `generate_narration.py` can be swapped for any edge-tts voice
  (`edge-tts --list-voices` shows all options) — worth alternating voices between the
  two daily runs if you want more variety.
- YouTube's free quota is 10,000 units/day; one upload costs ~1,600 units, so 2
  uploads/day uses well under the daily limit — there's headroom to go to 3-4/day
  if you want to scale further.
- `config/topics.json` has 40 topics, which at 2/day is a 20-day cycle before
  repeats — add more any time; no code changes needed.
