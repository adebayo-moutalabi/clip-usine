#!/usr/bin/env python3
"""Radar Twitch — qui streame le jeu en français, et quels clips montent.

Produit docs/data/radar.json, lu par la page GitHub Pages.
Besoin de deux secrets GitHub : TWITCH_CLIENT_ID et TWITCH_CLIENT_SECRET
(gratuits, créés sur dev.twitch.tv/console).
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.twitch.tv/helix"
GAME_NAMES = [n.strip() for n in os.environ.get(
    "GAME_NAMES", "Grand Theft Auto VI,Grand Theft Auto 6").split(",") if n.strip()]
LANG = os.environ.get("RADAR_LANG", "fr")
HOURS = int(os.environ.get("RADAR_HOURS", "24"))
MAX_CHANNELS = int(os.environ.get("RADAR_CHANNELS", "40"))
OUT = Path(os.environ.get("RADAR_OUT", "docs/data/radar.json"))


def http(url, headers=None, data=None):
    req = urllib.request.Request(url, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def token(cid, secret):
    body = urllib.parse.urlencode(
        {"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}).encode()
    return http("https://id.twitch.tv/oauth2/token", data=body)["access_token"]


def get(path, params, headers, pages=1):
    """GET paginé ; `params` est une liste de couples (clé, valeur)."""
    items, cursor = [], None
    for _ in range(pages):
        q = list(params) + ([("after", cursor)] if cursor else [])
        res = http(f"{API}/{path}?{urllib.parse.urlencode(q)}", headers)
        items += res.get("data", [])
        cursor = (res.get("pagination") or {}).get("cursor")
        if not cursor:
            break
    return items


def find_game(headers):
    for name in GAME_NAMES:
        data = get("games", [("name", name)], headers)
        if data:
            return data[0]
    for name in GAME_NAMES:  # recherche approchée en secours
        data = get("search/categories", [("query", name), ("first", 5)], headers)
        if data:
            return data[0]
    return None


def score_prospect(viewers):
    """Les chaînes moyennes sont les meilleures cibles : assez d'audience pour
    payer un monteur, pas assez pour avoir déjà une équipe."""
    if 30 <= viewers <= 800:
        return 2
    if 800 < viewers <= 3000:
        return 1
    return 0


def build(headers, now=None):
    now = now or datetime.now(timezone.utc)
    game = find_game(headers)
    if not game:
        return {"updated": now.isoformat(), "game": None, "streams": [], "clips": [],
                "note": "Catégorie introuvable : vérifie GAME_NAMES."}
    gid = game["id"]
    streams = get("streams", [("game_id", gid), ("language", LANG), ("first", 100)], headers, pages=2)
    streams.sort(key=lambda s: s.get("viewer_count", 0), reverse=True)

    # mémoire des chaînes vues (pour suivre leurs clips même hors ligne)
    seen = {}
    if OUT.exists():
        try:
            seen = {c["id"]: c for c in json.loads(OUT.read_text()).get("channels", [])}
        except (ValueError, KeyError):
            seen = {}
    for s in streams:
        prev = seen.get(s["user_id"], {})
        seen[s["user_id"]] = {"id": s["user_id"], "login": s["user_login"], "name": s["user_name"],
                              "peak": max(prev.get("peak", 0), s.get("viewer_count", 0)),
                              "last_seen": now.isoformat()}
    channels = sorted(seen.values(), key=lambda c: c["peak"], reverse=True)[:300]

    since = (now - timedelta(hours=HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    clips = []
    for ch in channels[:MAX_CHANNELS]:
        try:
            data = get("clips", [("broadcaster_id", ch["id"]), ("started_at", since),
                                 ("ended_at", until), ("first", 20)], headers)
        except urllib.error.HTTPError as e:
            print(f"clips {ch['login']} : HTTP {e.code}", file=sys.stderr)
            continue
        clips += [c for c in data if c.get("game_id") == gid]

    def age_h(c):
        t = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
        return max((now - t).total_seconds() / 3600, 0.25)

    out_clips = [{
        "title": c.get("title", ""), "url": c["url"], "channel": c.get("broadcaster_name", ""),
        "views": c.get("view_count", 0), "duration": c.get("duration", 0),
        "created_at": c["created_at"], "thumb": c.get("thumbnail_url", ""),
        "per_hour": round(c.get("view_count", 0) / age_h(c), 1),
    } for c in clips]
    out_clips.sort(key=lambda c: c["per_hour"], reverse=True)

    return {
        "updated": now.isoformat(),
        "game": {"id": gid, "name": game.get("name", "")},
        "lang": LANG, "hours": HOURS,
        "streams": [{
            "name": s["user_name"], "login": s["user_login"], "viewers": s.get("viewer_count", 0),
            "title": s.get("title", ""), "started_at": s.get("started_at", ""),
            "prospect": score_prospect(s.get("viewer_count", 0)),
        } for s in streams[:100]],
        "clips": out_clips[:60],
        "channels": channels,
    }


def main():
    cid, secret = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    if not cid or not secret:
        print("Secrets TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET absents : radar ignoré.")
        return
    headers = {"Client-Id": cid, "Authorization": f"Bearer {token(cid, secret)}"}
    data = build(headers)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(data['streams'])} lives, {len(data['clips'])} clips -> {OUT}")


if __name__ == "__main__":
    main()
