"""State persistence for system-monitor."""

import json
from pathlib import Path


def load_state(path: Path) -> dict:
    """Load persisted status from previous run."""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"services": {}}


def save_state(state: dict, path: Path) -> None:
    """Atomically persist current state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(path)
