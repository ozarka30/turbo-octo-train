"""Long-term memory: what the learner has been taught, across sessions.

Stored in a small SQLite database (default ~/.jptutor/memory.sqlite). Three
tables: sentences (with the full lesson, so a repeated line can be replayed
without calling Claude), pieces (vocabulary and particles with how often they
have come up), and patterns (the takeaways). `summary()` renders the tiers the
tutor prompt uses: known well, still learning, patterns taught, recent lines.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .lesson import Lesson

KNOWN_AFTER = 3  # times a piece must come up before it counts as known

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sentences (
    key TEXT PRIMARY KEY,
    japanese TEXT NOT NULL,
    english TEXT NOT NULL,
    game TEXT NOT NULL DEFAULT '',
    speaker TEXT NOT NULL DEFAULT '',
    lesson_json TEXT NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pieces (
    japanese TEXT NOT NULL,
    reading TEXT NOT NULL,
    meaning TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    times_seen INTEGER NOT NULL DEFAULT 1,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    last_sentence TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (japanese, reading)
);
CREATE TABLE IF NOT EXISTS usage (
    ts REAL NOT NULL,
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    kind TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    cache_read INTEGER NOT NULL,
    cache_write INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL
);
CREATE TABLE IF NOT EXISTS patterns (
    text TEXT PRIMARY KEY,
    times_seen INTEGER NOT NULL DEFAULT 1,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL
);
"""


def sentence_key(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


@dataclass
class StoredSentence:
    japanese: str
    english: str
    lesson: Lesson
    times_seen: int
    last_seen: float
    game: str = ""


@dataclass
class KnowledgeSummary:
    known: List[str] = field(default_factory=list)  # "に (to, toward)"
    learning: List[str] = field(default_factory=list)
    patterns: List[str] = field(default_factory=list)
    recent_sentences: List[str] = field(default_factory=list)  # "japanese = english"
    total_pieces: int = 0
    total_sentences: int = 0

    def render(self) -> str:
        if not (self.known or self.learning or self.patterns or self.recent_sentences):
            return "This is the learner's first lesson. Nothing has been taught yet."
        parts = []
        parts.append(f"The learner has had {self.total_sentences} sentences and met {self.total_pieces} pieces so far.")
        if self.known:
            parts.append("Known well, seen many times, do not re-explain, just use them: " + "; ".join(self.known))
        if self.learning:
            parts.append("Met once or twice, a few words of reminder is enough: " + "; ".join(self.learning))
        if self.patterns:
            parts.append("Patterns already taught, refer back rather than teach again: " + " | ".join(self.patterns))
        if self.recent_sentences:
            parts.append("Most recent sentences, newest last: " + " | ".join(self.recent_sentences))
        return "\n".join(parts)


class Memory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------------ write
    def record_lesson(self, lesson: Lesson, *, game: str = "", speaker: str = "", now: Optional[float] = None, key_text: str = "") -> None:
        """Store a lesson. `key_text` is the sentence as OCR produced it, so later lookups
        (which also use OCR text) hit even if Claude normalised punctuation."""
        now = time.time() if now is None else now
        key = sentence_key(key_text or lesson.japanese)
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO sentences (key, japanese, english, game, speaker, lesson_json, times_seen, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET times_seen = times_seen + 1, last_seen = excluded.last_seen,
                       lesson_json = excluded.lesson_json, english = excluded.english""",
                (key, lesson.japanese, lesson.english, game, speaker, lesson.model_dump_json(), now, now),
            )
            for c in lesson.chunks:
                self._db.execute(
                    """INSERT INTO pieces (japanese, reading, meaning, note, times_seen, first_seen, last_seen, last_sentence)
                       VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                       ON CONFLICT(japanese, reading) DO UPDATE SET times_seen = times_seen + 1, last_seen = excluded.last_seen,
                           meaning = excluded.meaning, last_sentence = excluded.last_sentence""",
                    (c.japanese, c.reading, c.meaning, c.note, now, now, lesson.japanese),
                )
            if lesson.pattern.strip():
                self._db.execute(
                    """INSERT INTO patterns (text, times_seen, first_seen, last_seen) VALUES (?, 1, ?, ?)
                       ON CONFLICT(text) DO UPDATE SET times_seen = times_seen + 1, last_seen = excluded.last_seen""",
                    (lesson.pattern.strip(), now, now),
                )

    def touch_sentence(self, japanese: str, now: Optional[float] = None) -> None:
        """Count another sighting of a line that was replayed from memory, and of its pieces."""
        now = time.time() if now is None else now
        key = sentence_key(japanese)
        with self._lock, self._db:
            row = self._db.execute("SELECT lesson_json FROM sentences WHERE key = ?", (key,)).fetchone()
            self._db.execute("UPDATE sentences SET times_seen = times_seen + 1, last_seen = ? WHERE key = ?", (now, key))
            if row is not None:
                lesson = Lesson.model_validate_json(row["lesson_json"])
                for c in lesson.chunks:
                    self._db.execute(
                        "UPDATE pieces SET times_seen = times_seen + 1, last_seen = ? WHERE japanese = ? AND reading = ?",
                        (now, c.japanese, c.reading),
                    )

    def forget(self) -> None:
        with self._lock, self._db:
            for table in ("sentences", "pieces", "patterns", "usage"):
                self._db.execute(f"DELETE FROM {table}")

    # ------------------------------------------------------------------ usage
    def record_usage(self, call) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO usage (ts, backend, model, kind, input_tokens, cache_read, cache_write, output_tokens, cost_usd) VALUES (?,?,?,?,?,?,?,?,?)",
                (call.ts, call.backend, call.model, call.kind, call.input_tokens, call.cache_read, call.cache_write, call.output_tokens, call.cost_usd),
            )

    def usage_totals(self) -> dict:
        with self._lock:
            r = self._db.execute(
                """SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens),0) AS input, COALESCE(SUM(cache_read),0) AS cache_read,
                          COALESCE(SUM(cache_write),0) AS cache_write, COALESCE(SUM(output_tokens),0) AS output,
                          COALESCE(SUM(cost_usd),0) AS cost, SUM(cost_usd IS NOT NULL) AS priced FROM usage"""
            ).fetchone()
        total_in = r["input"] + r["cache_read"] + r["cache_write"]
        return {**dict(r), "cached_pct": (100.0 * r["cache_read"] / total_in) if total_in else 0.0}

    # ------------------------------------------------------------------- read
    def lookup_sentence(self, japanese: str) -> Optional[StoredSentence]:
        with self._lock:
            row = self._db.execute("SELECT * FROM sentences WHERE key = ?", (sentence_key(japanese),)).fetchone()
        if row is None:
            return None
        return StoredSentence(
            japanese=row["japanese"], english=row["english"], lesson=Lesson.model_validate_json(row["lesson_json"]),
            times_seen=row["times_seen"], last_seen=row["last_seen"], game=row["game"],
        )

    def summary(self, *, known_limit: int = 120, learning_limit: int = 40, pattern_limit: int = 15, sentence_limit: int = 5) -> KnowledgeSummary:
        with self._lock:
            known = self._db.execute(
                "SELECT japanese, meaning FROM pieces WHERE times_seen >= ? ORDER BY last_seen DESC LIMIT ?", (KNOWN_AFTER, known_limit)
            ).fetchall()
            learning = self._db.execute(
                "SELECT japanese, meaning FROM pieces WHERE times_seen < ? ORDER BY last_seen DESC LIMIT ?", (KNOWN_AFTER, learning_limit)
            ).fetchall()
            patterns = self._db.execute("SELECT text FROM patterns ORDER BY last_seen DESC LIMIT ?", (pattern_limit,)).fetchall()
            recent = self._db.execute(
                "SELECT japanese, english FROM sentences ORDER BY last_seen DESC LIMIT ?", (sentence_limit,)
            ).fetchall()
            total_pieces = self._db.execute("SELECT COUNT(*) FROM pieces").fetchone()[0]
            total_sentences = self._db.execute("SELECT COUNT(*) FROM sentences").fetchone()[0]
        return KnowledgeSummary(
            known=[f"{r['japanese']} ({r['meaning']})" for r in known],
            learning=[f"{r['japanese']} ({r['meaning']})" for r in learning],
            patterns=[r["text"] for r in patterns],
            recent_sentences=[f"{r['japanese']} = {r['english']}" for r in reversed(recent)],
            total_pieces=total_pieces,
            total_sentences=total_sentences,
        )

    def stats(self) -> dict:
        with self._lock:
            s = self._db.execute("SELECT COUNT(*) AS n, COALESCE(SUM(times_seen), 0) AS seen, MAX(last_seen) AS last FROM sentences").fetchone()
            p = self._db.execute("SELECT COUNT(*) AS n, SUM(times_seen >= ?) AS known FROM pieces", (KNOWN_AFTER,)).fetchone()
            pat = self._db.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
            games = [r[0] for r in self._db.execute("SELECT DISTINCT game FROM sentences WHERE game != '' ORDER BY game").fetchall()]
        return {
            "sentences": s["n"], "sentence_sightings": s["seen"], "last_lesson": s["last"],
            "pieces": p["n"], "pieces_known": p["known"] or 0, "patterns": pat, "games": games,
        }

    def pieces(self, *, order: str = "last_seen", limit: int = 0) -> List[sqlite3.Row]:
        col = {"last_seen": "last_seen DESC", "count": "times_seen DESC, last_seen DESC", "first_seen": "first_seen ASC"}[order]
        sql = f"SELECT * FROM pieces ORDER BY {col}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            return self._db.execute(sql).fetchall()

    def sentences(self, *, limit: int = 0) -> List[sqlite3.Row]:
        sql = "SELECT * FROM sentences ORDER BY last_seen DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            return self._db.execute(sql).fetchall()

    # ----------------------------------------------------------------- export
    def export_anki(self, path: Path) -> int:
        """Tab-separated file Anki can import: front = Japanese, back = reading, meaning, note, example."""
        rows = self.pieces(order="first_seen")
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                back = f"{r['reading']}<br>{r['meaning']}"
                if r["note"]:
                    back += f"<br><i>{r['note']}</i>"
                if r["last_sentence"]:
                    back += f"<br>{r['last_sentence']}"
                f.write(f"{r['japanese']}\t{back}\n")
        return len(rows)
