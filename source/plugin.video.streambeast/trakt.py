"""Trakt device sign-in and authenticated watchlist access."""
import json
import time
from urllib.error import HTTPError

from catalog import request_json

BASE = "https://api.trakt.tv"


class Trakt:
    def __init__(self, addon):
        self.addon = addon
        self.client_id = addon.getSetting("trakt_client")
        self.secret = addon.getSetting("trakt_secret")

    def request(self, path, data=None, token=None):
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id,
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        return request_json(BASE + path, headers, None if data is None else json.dumps(data).encode())

    def save(self, data):
        self.addon.setSetting("trakt_access", data["access_token"])
        self.addon.setSetting("trakt_refresh", data["refresh_token"])
        self.addon.setSetting(
            "trakt_expiry", str(int(time.time()) + int(data["expires_in"]) - 60)
        )

    def connect(self, progress, monitor):
        code = self.request("/oauth/device/code", {"client_id": self.client_id})
        lifetime = int(code["expires_in"])
        interval = max(1, int(code["interval"]))
        start = time.monotonic()
        next_poll = start + interval
        prompt = "Open {} and enter {}".format(code["verification_url"], code["user_code"])
        while time.monotonic() - start < lifetime:
            if progress.iscanceled() or monitor.waitForAbort(0.25):
                return False
            now = time.monotonic()
            progress.update(min(100, int((now - start) * 100 / lifetime)), prompt)
            if now < next_poll:
                continue
            try:
                self.save(self.request("/oauth/device/token", {
                    "code": code["device_code"], "client_id": self.client_id,
                    "client_secret": self.secret,
                }))
                return True
            except HTTPError as error:
                if error.code == 429:
                    interval += 5
                elif error.code in {404, 409, 410, 418}:
                    if error.code != 404:
                        return False
                elif error.code != 400:
                    raise
            next_poll = time.monotonic() + interval
        return False

    def watchlist(self):
        token = self.addon.getSetting("trakt_access")
        if not token:
            raise ValueError("Connect Trakt first.")
        expiry = int(self.addon.getSetting("trakt_expiry") or 0)
        refresh = expiry <= time.time()
        for attempt in range(2):
            if refresh:
                data = self.request("/oauth/token", {
                    "refresh_token": self.addon.getSetting("trakt_refresh"),
                    "client_id": self.client_id, "client_secret": self.secret,
                    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob", "grant_type": "refresh_token",
                })
                self.save(data)
                token = data["access_token"]
            try:
                return self.request("/sync/watchlist/movies", token=token) + self.request(
                    "/sync/watchlist/shows", token=token
                )
            except HTTPError as error:
                if error.code != 401 or attempt or refresh:
                    raise
                refresh = True
