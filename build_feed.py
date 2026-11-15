import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
DATA_DIR = os.path.join(ROOT, "data")
DOCS_DIR = os.path.join(ROOT, "docs")
RESOLVED_PATH = os.path.join(DATA_DIR, "channels_resolved.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
QUOTA_PATH = os.path.join(DATA_DIR, "quota.json")
TODAY_PATH = os.path.join(DOCS_DIR, "today.json")

API = "https://www.googleapis.com/youtube/v3/"
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id="
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}
COSTS = {"channels": 1, "videos": 1, "search": 100}
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
UNITS = {}


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def save_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def http_get(url, retries=3):
    last = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "video-feed/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            last = error
            if error.code in (400, 401, 403, 404):
                break
            time.sleep(2 ** attempt)
        except Exception as error:
            last = error
            time.sleep(2 ** attempt)
    raise last


def api(endpoint, params, key):
    UNITS[endpoint] = UNITS.get(endpoint, 0) + COSTS[endpoint]
    query = urllib.parse.urlencode({**params, "key": key})
    return json.loads(http_get(API + endpoint + "?" + query))


def parse_duration(text):
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", text or "")
    if not match:
        return 0
    days, hours, minutes, seconds = (int(part) if part else 0 for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def retry_after(day):
    return (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def resolve_channel(channel, cache, key, day):
    label = channel.get("handle") or channel["name"]
    cached = cache.get(label)
    if cached and cached.get("id"):
        return cached
    if cached and cached.get("failed_on", "") >= retry_after(day):
        return None
    found = None
    if channel.get("handle"):
        data = api("channels", {"part": "id,snippet", "forHandle": "@" + channel["handle"]}, key)
        items = data.get("items", [])
        if items:
            found = {"id": items[0]["id"], "title": items[0]["snippet"]["title"], "via": "handle"}
    if not found:
        data = api("search", {"part": "snippet", "type": "channel", "q": channel["name"], "maxResults": 1}, key)
        items = data.get("items", [])
        if items:
            found = {
                "id": items[0]["id"]["channelId"],
                "title": items[0]["snippet"]["title"],
                "via": "search",
            }
    if found:
        cache[label] = found
        print("résolu : %s -> %s (%s)" % (channel["name"], found["title"], found["via"]))
    else:
        cache[label] = {"id": None, "failed_on": day}
        print("introuvable : " + channel["name"])
    return found


def fetch_feed(channel_id):
    root = ET.fromstring(http_get(FEED + channel_id))
    entries = []
    for entry in root.findall("a:entry", NS):
        video_id = entry.findtext("yt:videoId", namespaces=NS)
        title = entry.findtext("a:title", namespaces=NS)
        published = entry.findtext("a:published", namespaces=NS)
        if not video_id or not VIDEO_ID.match(video_id) or not title or not published:
            continue
        entries.append(
            {
                "id": video_id,
                "title": title,
                "published": datetime.fromisoformat(published.replace("Z", "+00:00")),
            }
        )
    return entries


def collect(channels, cache, settings, now, blocked, key, warnings, day):
    cutoff = now - timedelta(days=settings["max_age_days"])
    entries = {}
    for channel in channels:
        try:
            info = resolve_channel(channel, cache, key, day)
        except Exception:
            warnings.append("Chaîne non résolue : " + channel["name"])
            continue
        if not info:
            warnings.append("Chaîne introuvable : " + channel["name"])
            continue
        try:
            feed = fetch_feed(info["id"])
        except Exception:
            warnings.append("Flux illisible : " + channel["name"])
            continue
        for item in feed:
            if item["published"] < cutoff or item["id"] in blocked:
                continue
            item["channel"] = info["title"]
            item["channel_id"] = info["id"]
            item["theme"] = channel["theme"]
            item["lang"] = channel.get("lang") or ""
            entries[item["id"]] = item
    return entries


def enrich(entries, settings, key):
    ids = list(entries)
    details = {}
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        data = api("videos", {"part": "contentDetails,status,snippet", "id": ",".join(chunk)}, key)
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            status = item.get("status", {})
            language = snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or ""
            details[item["id"]] = {
                "duration_s": parse_duration(item.get("contentDetails", {}).get("duration")),
                "embeddable": bool(status.get("embeddable", True)),
                "public": status.get("privacyStatus") == "public",
                "live": snippet.get("liveBroadcastContent", "none"),
                "lang": language[:2].lower(),
            }
    candidates = []
    for video_id, entry in entries.items():
        detail = details.get(video_id)
        if not detail:
            continue
        if detail["duration_s"] < settings["min_duration_s"]:
            continue
        if detail["duration_s"] <= settings["shorts_max_s"]:
            continue
        if not detail["embeddable"] or not detail["public"] or detail["live"] != "none":
            continue
        entry["duration_s"] = detail["duration_s"]
        if detail["lang"]:
            entry["lang"] = detail["lang"]
        candidates.append(entry)
    return candidates


def score_candidates(candidates, themes, day, now, settings):
    rng = random.Random(day)
    bonus_theme = themes[datetime.strptime(day, "%Y-%m-%d").toordinal() % len(themes)]
    for candidate in candidates:
        age_days = (now - candidate["published"]).total_seconds() / 86400
        freshness = 0.5 ** (age_days / settings["half_life_days"])
        candidate["score"] = freshness + rng.random() * 0.35 + (0.25 if candidate["theme"] == bonus_theme else 0.0)
    return sorted(candidates, key=lambda item: (-item["score"], item["id"]))


def select(ordered, settings):
    wanted = settings["videos_per_day"]
    chosen = []
    channels_used = set()
    theme_count = {}

    def sweep(respect_theme, respect_channel):
        for candidate in ordered:
            if len(chosen) >= wanted:
                return
            if candidate in chosen:
                continue
            if respect_channel and candidate["channel_id"] in channels_used:
                continue
            if respect_theme and theme_count.get(candidate["theme"], 0) >= settings["max_per_theme"]:
                continue
            chosen.append(candidate)
            channels_used.add(candidate["channel_id"])
            theme_count[candidate["theme"]] = theme_count.get(candidate["theme"], 0) + 1

    sweep(True, True)
    sweep(False, True)
    sweep(False, False)
    return chosen


def build_payload(chosen, day, now, warnings, theme_labels):
    videos = []
    for item in chosen:
        videos.append(
            {
                "id": item["id"],
                "title": item["title"],
                "channel": item["channel"],
                "theme": item["theme"],
                "lang": item["lang"],
                "duration_s": item["duration_s"],
                "published_at": item["published"].isoformat(),
                "thumb": "https://i.ytimg.com/vi/%s/hqdefault.jpg" % item["id"],
            }
        )
    return {
        "day": day,
        "generated_at": now.isoformat(),
        "theme_labels": theme_labels,
        "warnings": warnings,
        "videos": videos,
    }


def main():
    force = "--force" in sys.argv or os.environ.get("FORCE", "").lower() == "true"
    key = os.environ.get("YT_API_KEY", "")
    if not key:
        print("YT_API_KEY manquante")
        return 2
    config = load_json(CONFIG_PATH, None)
    if not config:
        print("config.json illisible")
        return 2
    settings = config["settings"]
    channels = config["channels"]
    themes = sorted({channel["theme"] for channel in channels})
    now = datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo(settings["timezone"])).strftime("%Y-%m-%d")

    current = load_json(TODAY_PATH, {})
    if current.get("day") == day and not force:
        print("liste du jour déjà générée : " + day)
        return 0

    history = load_json(HISTORY_PATH, {"shown": {}})
    oldest = (now - timedelta(days=settings["history_days"])).astimezone(ZoneInfo(settings["timezone"])).strftime("%Y-%m-%d")
    shown = {video_id: seen for video_id, seen in history["shown"].items() if seen >= oldest}
    blocked = {video_id for video_id, seen in shown.items() if seen != day}

    cache = load_json(RESOLVED_PATH, {})
    warnings = []
    entries = collect(channels, cache, settings, now, blocked, key, warnings, day)
    save_json(RESOLVED_PATH, cache)

    if not entries:
        print("aucun candidat : flux ou clé API en cause")
        return 1

    candidates = enrich(entries, settings, key)
    if not candidates:
        print("aucun candidat après filtres")
        return 1

    covered = {candidate["theme"] for candidate in candidates}
    for theme in themes:
        if theme not in covered:
            warnings.append("Aucune vidéo récente pour le thème : " + config["theme_labels"].get(theme, theme))

    ordered = score_candidates(candidates, themes, day, now, settings)
    chosen = select(ordered, settings)
    if len(chosen) < settings["videos_per_day"]:
        warnings.append("Seulement %d vidéo(s) disponible(s) aujourd'hui" % len(chosen))

    for item in chosen:
        shown[item["id"]] = day
    save_json(HISTORY_PATH, {"shown": shown})

    quota = load_json(QUOTA_PATH, {})
    quota[day] = {**UNITS, "total": sum(UNITS.values())}
    for old in sorted(quota)[:-30]:
        del quota[old]
    save_json(QUOTA_PATH, quota)

    save_json(TODAY_PATH, build_payload(chosen, day, now, warnings, config["theme_labels"]))
    print("liste écrite : %d vidéo(s), %d avertissement(s)" % (len(chosen), len(warnings)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
