"""Watch Kodi playback only while a StreamBeast request is active."""
import json
from time import time
from urllib.parse import urlencode

import xbmc
import xbmcgui

from playback import HANDOFF, save


class Player(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.events = []

    def onAVStarted(self):
        try:
            self.events.append(("started", self.getPlayingFile()))
        except RuntimeError:
            pass

    def onPlayBackError(self):
        self.events.append(("error", ""))

    def onPlayBackStopped(self):
        self.events.append(("stopped", ""))

    def onPlayBackEnded(self):
        self.events.append(("ended", ""))


def record(active, position, complete=False):
    try:
        save(active, position, complete)
    except OSError:
        pass


def main():
    monitor, player, window = xbmc.Monitor(), Player(), xbmcgui.Window(10000)
    pending = active = None
    previous = ""
    position = saved = 0
    while not monitor.waitForAbort(0.25):
        raw = window.getProperty(HANDOFF)
        if raw and raw != previous:
            previous = raw
            try:
                pending = json.loads(raw)
            except ValueError:
                pending = None
        if pending and time() - pending["time"] > 25:
            pending = None
        while player.events:
            event, path = player.events.pop(0)
            if event == "started":
                if pending and path == pending["url"]:
                    active, pending = pending, None
                    position = saved = 0
                elif active and path != active["url"]:
                    active = None
            elif event == "error" and pending:
                retry = pending.get("retry")
                pending = None
                if retry:
                    path = "plugin://plugin.video.streambeast/?" + urlencode(retry)
                    xbmc.executebuiltin('PlayMedia("{}")'.format(path))
            elif event in {"ended", "stopped", "error"}:
                replacing = bool(active and pending)
                if active:
                    if active["params"]["kind"] != "sports" and (position >= 20 or event == "ended"):
                        record(active, position, event == "ended")
                    active = None
                if event == "stopped" and not replacing:
                    pending = None
        if active and active["params"]["kind"] != "sports" and player.isPlayingVideo():
            try:
                position = int(player.getTime())
            except RuntimeError:
                continue
            if position >= 20 and abs(position - saved) >= 15:
                record(active, position)
                saved = position


if __name__ == "__main__":
    main()
