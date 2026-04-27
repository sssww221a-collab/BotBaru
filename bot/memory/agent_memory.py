"""
Agent memory — persistent cross-game learning via molty-royale-context.json.
Two sections: `overall` (persistent) and `temp` (per-game).
"""
import json
from pathlib import Path
from typing import Optional
from bot.config import MEMORY_DIR, MEMORY_FILE
from bot.utils.logger import get_logger

log = get_logger(__name__)

DEFAULT_MEMORY = {
    "overall": {
        "identity": {"name": "", "playstyle": "adaptive guardian hunter"},
        "strategy": {
            "deathzone": "move inward before turn 5",
            "guardians": "engage immediately — highest sMoltz value",
            "weather": "avoid combat in fog or storm",
            "ep_management": "rest when EP < 4 before engaging",
        },
        "history": {
            "totalGames": 0,
            "wins": 0,
            "avgKills": 0.0,
            "lessons": [],
        },
    },
    "temp": {},
}


class AgentMemory:
    """Read/write molty-royale-context.json with overall + temp sections."""

    def __init__(self, memory_file: Optional[Path] = None):
        self.memory_file = memory_file or MEMORY_FILE
        self.data = dict(DEFAULT_MEMORY)
        self._loaded = False

    async def load(self):
        """Load memory from disk. Create default if missing."""
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        if self.memory_file.exists():
            try:
                raw = MEMORY_FILE.read_text(encoding="utf-8")
                self.data = json.loads(raw)
                self._loaded = True
                log.info("Memory loaded: %d games, %d lessons",
                         self.data["overall"]["history"]["totalGames"],
                         len(self.data["overall"]["history"]["lessons"]))
            except (json.JSONDecodeError, KeyError) as e:
                log.warning("Memory file corrupt, using defaults: %s", e)
                self.data = dict(DEFAULT_MEMORY)
        else:
            log.info("No memory file — starting fresh")

    async def save(self):
        """Persist memory to disk."""
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self.memory_file.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.debug("Memory saved to %s", self.memory_file)

    def set_agent_name(self, name: str):
        self.data["overall"]["identity"]["name"] = name

    def get_strategy(self) -> dict:
        return self.data.get("overall", {}).get("strategy", {})

    def get_lessons(self) -> list:
        return self.data.get("overall", {}).get("history", {}).get("lessons", [])

    # ── Temp (per-game) ───────────────────────────────────────────────

    def set_temp_game(self, game_id: str):
        self.data["temp"] = {
            "gameId": game_id,
            "currentStrategy": "adaptive",
            "knownAgents": [],
            "notes": "",
            "path_history": [],  # Jejak kaki: list 20 region IDs terakhir
            "junk_blacklist": set(),  # Memori sampah: item IDs yang sudah dibuang
        }

    def update_temp_note(self, note: str):
        if "temp" not in self.data:
            self.data["temp"] = {}
        existing = self.data["temp"].get("notes", "")
        self.data["temp"]["notes"] = f"{existing}\n{note}".strip()

    def add_path_history(self, region_id: str, max_history: int = 20):
        """Tambah jejak ruangan, jaga maksimal 20."""
        if "temp" not in self.data:
            return
        history = self.data["temp"].get("path_history", [])
        if region_id not in history:
            history.append(region_id)
            if len(history) > max_history:
                history.pop(0)
        self.data["temp"]["path_history"] = history

    def get_path_history(self) -> list:
        """Ambil jejak ruangan."""
        return self.data.get("temp", {}).get("path_history", [])

    def add_junk_blacklist(self, item_id: str):
        """Tambah item ID ke blacklist sampah."""
        if "temp" not in self.data:
            return
        blacklist = self.data["temp"].get("junk_blacklist", set())
        blacklist.add(item_id)
        self.data["temp"]["junk_blacklist"] = list(blacklist)  # JSON serializable

    def is_junk_blacklisted(self, item_id: str) -> bool:
        """Cek apakah item ID di blacklist."""
        blacklist = self.data.get("temp", {}).get("junk_blacklist", [])
        return item_id in blacklist

    # ── History update (after game end) ───────────────────────────────

    def record_game_end(self, is_winner: bool, final_rank: int,
                        kills: int, smoltz_earned: int = 0):
        history = self.data["overall"]["history"]
        history["totalGames"] += 1
        if is_winner:
            history["wins"] += 1

        # Rolling average kills
        total = history["totalGames"]
        old_avg = history["avgKills"]
        history["avgKills"] = round(((old_avg * (total - 1)) + kills) / total, 2)

    def add_lesson(self, lesson: str, max_lessons: int = 20):
        """Append a new lesson, keeping max_lessons most recent."""
        lessons = self.data["overall"]["history"]["lessons"]
        if lesson not in lessons:
            lessons.append(lesson)
            if len(lessons) > max_lessons:
                lessons.pop(0)
