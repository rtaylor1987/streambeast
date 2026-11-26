"""Short-lived playback handoff and local resume history."""
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import xbmcaddon
import xbmcgui
import xbmcvfs

HANDOFF = "StreamBeast.Playback"
IDENTITY_FIELDS = ("kind", "id", "season", "episode", "action")


def identity(params):
    return {key: str(params[key]) for key in IDENTITY_FIELDS if key in params}


def history_path():
    addon = xbmcaddon.Addon("plugin.video.streambeast")
    return Path(xbmcvfs.translatePath(addon.getAddonInfo("profile"))) / "resume.json"


def history():
    try:
        records = json.loads(history_path().read_text(encoding="utf-8"))
        return records if isinstance(records, list) else []
    except (OSError, ValueError):
        return []


def save(data, position, complete=False):
    path = history_path()
    rows = [row for row in history() if row["params"] != data["params"]]
    if not complete:
        rows.insert(0, {"params": data["params"], "title": data["title"],
                        "art": data.get("art", {}), "position": position})
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as file:
        json.dump(rows[:24], file)
    temporary = Path(file.name)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def handoff(data):
    xbmcgui.Window(10000).setProperty(HANDOFF, json.dumps(data))
