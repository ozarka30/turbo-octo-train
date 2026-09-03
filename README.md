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

## Setup

```bash
git clone <this repo> && cd turbo-octo-train
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env    # put your ANTHROPIC_API_KEY in it, then `export $(cat .env)` or use direnv
```

Audio playback needs one of: `pip install pygame`, or `ffmpeg` (`ffplay`), `mpg123`, or `mpv` on your PATH. macOS has `afplay` built in.

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
2. Find the dialogue box: `jptutor select-region` (drag a box, it prints `JPTUTOR_REGION=x,y,w,h`), or `jptutor snapshot` and read the pixels off the saved image.
3. Watch:

```bash
export JPTUTOR_REGION=0,800,1920,280
jptutor watch --context "Pokemon Scarlet"          # automatic: fires when the box changes and settles
jptutor watch --manual                              # press Enter whenever you want the current line taught
jptutor watch --quick                               # Japanese + English only, no breakdown
jptutor watch --level intermediate                  # beginner | intermediate | advanced
```

You can also run the pipeline on saved screenshots: `jptutor image shot1.png shot2.png`.

### How a session behaves

- Lines already taught are remembered and skipped when the game re-renders them.
- Every chunk taught is passed back to Claude on the next line, so it says "you already know ni" instead of re-explaining it.
- Screen capture keeps running while a lesson is being spoken. If you race ahead, the oldest queued frames are dropped rather than played back late.
- Menus, HUD numbers, and button prompts are OCR'd but not taught. Only `dialogue` and `narration` lines get a lesson.

## Configuration

All settings are environment variables (see `.env.example`); the common ones also have CLI flags.

| Variable                   | Default              | Meaning                                              |
| -------------------------- | -------------------- | ---------------------------------------------------- |
| `ANTHROPIC_API_KEY`        |                      | Required                                             |
| `JPTUTOR_TUTOR_MODEL`      | `claude-opus-5`      | Model that writes the lesson                         |
| `JPTUTOR_OCR_MODEL`        | `claude-opus-5`      | Model that reads the screenshot                      |
| `JPTUTOR_TUTOR_EFFORT`     | `high`               | `low` to `max`; lower is cheaper and faster          |
| `JPTUTOR_OCR_EFFORT`       | `low`                |                                                      |
| `JPTUTOR_LEVEL`            | `beginner`           | How much the tutor assumes you know                  |
| `JPTUTOR_JA_VOICE`         | `ja-JP-NanamiNeural` | Try `ja-JP-KeitaNeural` for a male voice             |
| `JPTUTOR_EN_VOICE`         | `en-US-AriaNeural`   |                                                      |
| `JPTUTOR_REGION`           | whole screen         | `x,y,width,height` of the dialogue box               |
| `JPTUTOR_POLL_INTERVAL`    | `0.5`                | Seconds between screen grabs                         |
| `JPTUTOR_CHANGE_THRESHOLD` | `0.02`               | Fraction of the region that must change to trigger   |

## How the lesson is shaped

Claude returns a `Lesson` (see `jptutor/lesson.py`), and `jptutor/script.py` turns it into the spoken sequence:

```
[ja slow] 学校に行きます。
[en]      I'm going to school.
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

The teaching style lives in one place, `TUTOR_SYSTEM` in `jptutor/prompts.py`. Edit it to change how the tutor talks.

## Project layout

```
jptutor/
  capture.py        screen grab + change detection (ChangeDetector, ScreenGrabber)
  claude_client.py  Claude calls: ocr(image) and teach(text) with structured outputs
  lesson.py         pydantic models: OcrResult, Lesson, Chunk, BuildStep
  prompts.py        system prompts
  script.py         Lesson -> ordered Utterances (the Paul Noble pacing)
  tts.py            edge-tts synthesis, disk cache, playback; ConsoleSpeaker for dry runs
  pipeline.py       OCR -> filter -> dedupe -> lesson -> speak; background FrameWorker
  cli.py            the `jptutor` command
  region_picker.py  drag-to-select overlay (tkinter)
  fake.py           canned backend for --offline and tests
tests/              unit tests, all offline (the Claude tests use a mock HTTP transport)
```

## Cost notes

Each new line costs one small vision call plus one lesson call. The system prompts are cached, so repeated calls are cheap on input. If cost matters more than lesson quality, set `JPTUTOR_OCR_MODEL=claude-sonnet-5` and/or `JPTUTOR_TUTOR_EFFORT=medium`. Requests opt into Anthropic's server-side refusal fallback, so a stray safety decline is retried on another model automatically instead of leaving a silent gap mid-game.

## Roadmap ideas

- Global hotkey (`pip install "jptutor[hotkey]"`) so Enter is not needed in manual mode.
- Pre-synthesise the next clip while the current one plays.
- A local OCR backend (manga-ocr) to skip the vision call entirely.
- Export a session's chunks to an Anki deck.

## Development

```bash
pip install -e ".[dev]"
pytest
```
