from __future__ import annotations

import os
import tempfile
from pathlib import Path

_STATE_DIR = Path(tempfile.mkdtemp(prefix="github-scout-tests-"))

# Keep the suite hermetic: never read or write the developer's real settings
# file or web history under %APPDATA%.
os.environ["GITHUB_SCOUT_SETTINGS_FILE"] = str(_STATE_DIR / "config.json")
os.environ["GITHUB_SCOUT_HISTORY_FILE"] = str(_STATE_DIR / "web_history.jsonl")
os.environ["GITHUB_SCOUT_NO_BROWSER"] = "1"
