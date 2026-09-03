# jptutor

An AI Japanese tutor that sits beside you while you play a game in Japanese.

When a line of text appears on screen, jptutor:

1. **Reads it aloud in Japanese** (slowly first, then at normal speed at the end).
2. **Gives the English translation.**
3. **Breaks the sentence down, Paul Noble style**: each word or particle with a memory hook, the word order read back piece by piece, then you rebuild the sentence yourself from a small chunk to the full line with a pause to think, and one reusable pattern to take away.

It is three parts glued together:

| Part   | What it does                                                | How                                              |
| ------ | ----------------------------------------------------------- | ------------------------------------------------ |
| Vision | Grabs the dialogue box, notices when the text changes, OCRs | `mss` + a cheap frame diff, then Claude vision   |
| AI     | Translates and writes the lesson                            | Claude Opus 5 with structured output (a `Lesson` JSON) |
| Voice  | Speaks Japanese and English                                 | `edge-tts` neural voices, cached to disk         |
| Overlay | Shows the sentence and highlights the piece being spoken   | always-on-top tkinter window, synced to the audio |
| Memory | Remembers what you have been taught, across sessions        | SQLite in `~/.jptutor/`, summarised into every lesson prompt |

## Setup

```bash
git clone <this repo> && cd turbo-octo-train
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
jptutor doctor          # tells you what is missing
```

Audio playback uses pygame, which is installed with the package. Put `ANTHROPIC_API_KEY` or any `JPTUTOR_*` setting in a `.env` file in the folder you run from and it is loaded automatically.

### Connecting to Claude

There are two backends. `jptutor` picks one automatically: the API if `ANTHROPIC_API_KEY` is set, otherwise Claude Code if the `claude` command is installed. Force one with `--backend` or `JPTUTOR_BACKEND`.

**Claude subscription (Pro/Max), via Claude Code.** The Anthropic API itself only accepts API keys, so the tutor runs on your subscription by shelling out to Claude Code's headless mode, `claude -p`, which uses the login you already have in Claude Code and returns validated JSON through `--json-schema`.

1. Install Claude Code: https://code.claude.com/docs/en/overview
2. Run `claude` once in a terminal and log in with your claude.ai account.
3. Make sure `ANTHROPIC_API_KEY` is **not** set in that shell, then `jptutor doctor` should say `backend: claude-code`.

Each line costs one `claude -p` process for OCR and one for the lesson, and both count against your plan's usage limits like any other Claude Code session. Measured on Opus at the default `high` effort: about 6 seconds for OCR and 30 to 40 seconds for the lesson. `JPTUTOR_TUTOR_EFFORT=medium` or `--model sonnet` speeds it up.

**API key.** Create a key at https://platform.claude.com/settings/keys, put it in `.env` (see `.env.example`), and export it. This path is faster per line and is billed per token to your Console workspace, separately from a claude.ai subscription.

## Try it without a game

```bash
# canned lesson, no API key, no audio: see the flow
jptutor teach --offline --dry-run "学校に行きます。"

# real lesson from Claude, spoken aloud
jptutor teach "この街には近づくな。"

# check your speakers and voices
jptutor say "こんにちは" --slow
jptutor voices --locale ja
```

## Play a game

1. Start the game in windowed or borderless mode.
2. Find the dialogue box: `jptutor select-region` (drag a box, it prints `JPTUTOR_REGION=x,y,w,h`), `jptutor snapshot` and read the pixels off the saved image, or pass `--auto-region` and let the first OCR result locate the box for you (it prints the region it settled on so you can save it).
3. Watch:

```bash
export JPTUTOR_REGION=0,800,1920,280
jptutor watch --context "Pokemon Scarlet"          # automatic: fires when the box changes and settles
jptutor watch --manual                              # press Enter whenever you want the current line taught
jptutor watch --quick                               # Japanese + English only, no breakdown
jptutor watch --level intermediate                  # beginner | intermediate | advanced
```

You can also run the pipeline on saved screenshots: `jptutor image shot1.png shot2.png`.

### Controls while you play

| Key | Action |
| --- | --- |
| F8  | Capture the screen now (no need for auto mode or Enter) |
| F9  | Skip the rest of this lesson |
| F10 | Pause / resume |
| F11 | Repeat the last lesson |
| F7  | Practice: say the last sentence and get checked (see below) |

Hotkeys are global, so they work while the game has focus. They need `pip install "jptutor[hotkey]"` (pynput); change them with `JPTUTOR_HOTKEY_MAP="skip=<ctrl>+s,pause=<f6>"` or turn them off with `--no-hotkeys`. Without pynput, `--manual` mode with Enter still works.

The Japanese line is read aloud as soon as OCR has it, while Claude is still writing the lesson, so the wait is filled with the thing you most want to hear. `JPTUTOR_PRESPEAK=0` turns that off.

### Voices

`edge-tts` gives the best voices but needs the network. `--tts auto` (the default) falls back to your operating system's voice for the rest of the session if edge-tts fails: `say` on macOS (install the Kyoko or Otoya voice), SAPI on Windows (add a Japanese voice under Settings > Language), `espeak-ng` on Linux. `--tts system` uses the OS voice from the start.

### The overlay

While a lesson plays, a small always-on-top window shows the sentence with the current piece highlighted, its kana reading underneath, and the English being spoken as a caption. The highlight follows the audio: the whole line while it is read, each chunk while it is explained, nothing while a rebuild question waits for your answer, then the answer.

- Drag it anywhere; press Esc on it to quit.
- It sits at the bottom centre of the primary screen by default. Move or resize it with `JPTUTOR_OVERLAY_GEOMETRY=x,y,w,h`, change the size with `JPTUTOR_OVERLAY_FONT_SIZE=40`, and the transparency with `JPTUTOR_OVERLAY_OPACITY=0.8`.
- Turn it off with `--no-overlay` or `JPTUTOR_OVERLAY=0`. `--dry-run` prints the same highlights as text instead.
- Run the game in windowed or borderless mode. An exclusive full-screen game draws over every other window, so the overlay will not show on top of it.
- It needs tkinter, which ships with python.org and Windows Python; on Debian or Ubuntu install `python3-tk`.

### Memory

The tutor keeps a long-term memory in `~/.jptutor/memory.sqlite`: every sentence with its full lesson, every piece (word or particle) with how many times it has come up, and every pattern taught. Before each new lesson, a summary goes to Claude in tiers, so the tutor behaves like someone who remembers you:

- **Known well** (a piece seen 3 or more times): used without explanation.
- **Met once or twice**: a few words of reminder, "ni again, pointing where you are heading".
- **Patterns already taught**: referred back to, not re-taught.
- Anything else is new and gets the full treatment.

Lines you met in an earlier session are handled by `--repeat` / `JPTUTOR_REPEAT`: `quick` (default) replays Japanese and English from memory with no Claude call until the line has been seen `JPTUTOR_REPEAT_SKIP_AFTER` times (3), then stays quiet; `skip` says nothing; `full` teaches it again. Replays count toward the pieces' sighting counts.

```bash
jptutor memory              # stats
jptutor memory vocab        # every piece with its count (--order count, --limit 50)
jptutor memory sentences    # every line taught
jptutor memory prompt       # exactly what the tutor is told about you
jptutor memory export       # Anki-importable tab file (--out deck.txt)
jptutor memory forget --yes # start over
```

`--no-memory` or `JPTUTOR_MEMORY=0` runs a session without reading or writing it; `JPTUTOR_MEMORY=/path/file.sqlite` keeps separate memories, for example one per game.

### How a session behaves

- Lines already taught are remembered and skipped when the game re-renders them.
- A dialogue box with several sentences is taught one sentence at a time, with the whole box passed along as context.
- Every sentence and chunk taught goes into memory and comes back to Claude on the next line, this session or a later one, so it says "you already know ni" instead of re-explaining it.
- Lines the OCR flags as still being typed out are skipped until they settle.
- Screen capture keeps running while a lesson is being spoken. If you race ahead, the oldest queued frames are dropped rather than played back late.
- Menus, HUD numbers, and button prompts are OCR'd but not taught. Only `dialogue`, `narration`, and `choice` lines get a lesson.

## Configuration

All settings are environment variables (see `.env.example`); the common ones also have CLI flags.

| Variable                   | Default              | Meaning                                              |
| -------------------------- | -------------------- | ---------------------------------------------------- |
| `ANTHROPIC_API_KEY`        |                      | Only for the `api` backend                           |
| `JPTUTOR_BACKEND`          | `auto`               | `api`, `claude-code` (subscription), or `auto`       |
| `JPTUTOR_TUTOR_MODEL`      | `claude-opus-5`      | Model that writes the lesson (`opus`/`sonnet` aliases work with `claude-code`) |
| `JPTUTOR_OCR_MODEL`        | `claude-opus-5`      | Model that reads the screenshot                      |
| `JPTUTOR_TUTOR_EFFORT`     | `high`               | `low` to `max`; lower is cheaper and faster          |
| `JPTUTOR_OCR_EFFORT`       | `low`                |                                                      |
| `JPTUTOR_LEVEL`            | `beginner`           | How much the tutor assumes you know                  |
| `JPTUTOR_JA_VOICE`         | `ja-JP-NanamiNeural` | Try `ja-JP-KeitaNeural` for a male voice             |
| `JPTUTOR_EN_VOICE`         | `en-US-AriaNeural`   |                                                      |
| `JPTUTOR_REGION`           | whole screen         | `x,y,width,height` of the dialogue box               |
| `JPTUTOR_POLL_INTERVAL`    | `0.5`                | Seconds between screen grabs                         |
| `JPTUTOR_CHANGE_THRESHOLD` | `0.02`               | Fraction of the region that must change to trigger   |
| `JPTUTOR_CACHE_TTL`        | `1h`                 | Prompt-cache TTL on the API backend: `5m` or `1h`    |
| `JPTUTOR_KNOWLEDGE_REFRESH`| `6`                  | Lessons between memory-summary snapshots (cached)    |
| `JPTUTOR_MEMORY`           | `~/.jptutor/memory.sqlite` | Memory file, or `0` to disable                 |
| `JPTUTOR_REPEAT`           | `quick`              | Line from an earlier session: `quick`, `skip`, `full`|
| `JPTUTOR_TTS`              | `auto`               | `edge`, `system`, or `auto` (edge with OS fallback)  |
| `JPTUTOR_PRESPEAK`         | `1`                  | Read the line aloud while the lesson is generated     |
| `JPTUTOR_HOTKEYS`          | `1`                  | `0` to skip registering global hotkeys               |
| `JPTUTOR_HOTKEY_MAP`       |                      | e.g. `skip=<f9>,pause=<f10>,repeat=<f11>,capture=<f8>,practice=<f7>` |
| `JPTUTOR_AUTO_REGION`      | `0`                  | Let the first OCR result pick the dialogue box       |
| `JPTUTOR_REPEAT_SKIP_AFTER`| `3`                  | Replay a known line until seen this many times       |
| `JPTUTOR_OVERLAY`          | `1`                  | `0` to disable the highlight window                  |
| `JPTUTOR_OVERLAY_GEOMETRY` | bottom centre        | `x,y,width,height` of the overlay                    |
| `JPTUTOR_OVERLAY_FONT_SIZE`| `34`                 | Japanese font size in the overlay                    |
| `JPTUTOR_OVERLAY_OPACITY`  | `0.88`               | Overlay transparency, 0 to 1                         |

## How the lesson is shaped

Claude returns a `Lesson` (see `jptutor/lesson.py`), and `jptutor/script.py` turns it into the spoken sequence:

```
[ja slow] 学校に行きます。
[en]      I'm going to school.
[en]      (tone, only when notable: "This is blunt, rough speech, the kind of order a soldier barks at you.")
[en]      Let's break that down.
[ja slow] がっこう
[en]      school. Gakkou. The kou at the end is the same kou as in koukou, high school.
[ja slow] に
[en]      to, toward. The little word ni points at where you are heading.
[ja slow] いきます
[en]      go, polite form. Iku means go. Swap the ku for ki, add masu, and you have the polite form.
[en]      So, piece by piece, it reads: school-to go-polite
[en]      So how would you say: to school?          (2 second pause to answer)
[ja]      がっこうに
[en]      And: I go to school?
[ja]      がっこうにいきます。
[en]      In Japanese the verb goes at the end. Say the where first, then the going.
[en]      Once more.
[ja]      学校に行きます。
[en]      I'm going to school.
```

The teaching style lives in one place, `jptutor/prompts.py`. Edit it to change how the tutor talks. The prompts are written around one fact: every field is spoken, Japanese fields by a Japanese voice and English fields by an English voice. So English fields must not contain kana or kanji (Japanese pieces are named in romaji, which the English voice can say), and readings are spelled the way they sound (the topic particle は is written わ) because the Japanese voice reads kana literally. Per-level guidance for beginner, intermediate, and advanced learners is in `LEVEL_GUIDANCE` in the same file.

## Project layout

```
jptutor/
  capture.py        screen grab + change detection (ChangeDetector, ScreenGrabber)
  backends.py       picks api vs claude-code
  claude_client.py  API backend: ocr(image) and teach(text) via the Anthropic SDK, structured outputs
  claude_code_backend.py  subscription backend: the same two calls through `claude -p --json-schema`
  lesson.py         pydantic models: OcrResult, Lesson, Chunk, BuildStep
  prompts.py        system prompts
  script.py         Lesson -> ordered Utterances (the Paul Noble pacing) with highlight spans
  display.py        Display interface, console highlighter, speaker wrapper that syncs them
  controls.py       skip / pause / repeat / capture / practice flags and global hotkeys
  memory.py         long-term memory (SQLite): sentences, pieces, patterns, tiered summary, usage log, Anki export
  cache.py          OCR results cached on disk by screenshot hash
  usage.py          token and cost accounting; the summary printed at exit
  overlay.py        the on-screen highlight window (tkinter)
  tts.py            edge-tts synthesis, disk cache, playback; ConsoleSpeaker for dry runs
  pipeline.py       OCR -> filter -> dedupe -> lesson -> speak; background FrameWorker
  cli.py            the `jptutor` command
  region_picker.py  drag-to-select overlay (tkinter)
  fake.py           canned backend for --offline and tests
tests/              unit tests, all offline (the Claude tests use a mock HTTP transport)
```

## Saving on usage

Each new line costs one small vision call plus one lesson call. Several layers keep that as cheap as possible:

- **Prompt caching (API backend).** Every request carries two cached system blocks: the frozen tutor prompt and a snapshot of your memory summary. The snapshot is refreshed only every 6 lessons (`JPTUTOR_KNOWLEDGE_REFRESH`), and what was taught since rides in the user message after the cache breakpoints, so the big part of the input is served from cache at a tenth of the price. The cache TTL is one hour by default (`JPTUTOR_CACHE_TTL=5m` for the cheaper short TTL if your lines never stop for more than five minutes), so the entry survives a battle or a stretch of exploring. On the subscription backend the same stable system prompt lets Claude Code's own prompt cache do the equivalent.
- **Memory replay.** A line you met in an earlier session is spoken from memory with no Claude call at all (`--repeat quick`, the default).
- **OCR cache.** Screenshots are hashed; an identical frame is never sent twice, even across restarts.
- **Change detection.** Nothing is sent until the dialogue box has changed and settled.
- **Usage report.** Every command prints a line at exit like `Claude usage this session: 4 calls, 14,210 input tokens (78% served from cache), 2,930 output tokens, about $0.11`, and `jptutor memory` shows the all-time total. On the subscription backend the dollar figure is Claude Code's own estimate of what the calls would have cost on the API.

The two levers that trade quality for cost are `JPTUTOR_TUTOR_EFFORT=medium` (less thinking per lesson) and `JPTUTOR_OCR_MODEL=claude-sonnet-5` (a cheaper model for the easy vision step). Requests on the API backend also opt into Anthropic's server-side refusal fallback, so a stray safety decline is retried on another model instead of leaving a silent gap mid-game.

## Roadmap ideas

- A local OCR backend (manga-ocr) to skip the vision call entirely.
- Real spaced repetition and a `jptutor review` mode that quizzes from memory without a game.

## Development

```bash
pip install -e ".[dev]"
pytest
```
