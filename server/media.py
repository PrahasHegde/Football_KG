"""
Media enrichment from Wikidata / Wikipedia: team flags and crests, player photos, bios and club careers.

Everything fetched is cached in app_data/media.sqlite (metadata) and app_data/img/ (image files), so each
lookup and each image is downloaded once. Images are served through /api/img (same origin): this keeps
the graph canvas exportable as PNG and lets the app work offline once the cache is warm.

Images come from Wikimedia Commons and are free-licensed (mostly CC BY-SA); the UI credits the source.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import itertools
import queue
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CACHE_DB = ROOT / "app_data" / "media.sqlite"
IMG_DIR = ROOT / "app_data" / "img"

# Wikimedia asks automated clients for a descriptive User-Agent.
# Wikimedia rate limits (2026): requests whose User-Agent carries no contact info (an email or a URL)
# are "unidentified" and limited to 10/min; with contact info the limit is 200/min. The contact is
# optional and comes from your .env (WIKIMEDIA_CONTACT) - nothing personal is sent unless you set it.
CONTACT = os.getenv("WIKIMEDIA_CONTACT", "").strip()
HEADERS = {
    "User-Agent": f"FootballKG/1.0 ({CONTACT}) python-urllib" if CONTACT
    else "FootballKG/1.0 (local football knowledge-graph explorer) python-urllib"
}
WIKI_API = "https://en.wikipedia.org/w/api.php"
# a Wikipedia page image that is a club crest, not a stadium or team photo
CREST_FILE = re.compile(r"\.(svg|png)$|logo|crest|badge|emblem|escudo|wappen", re.I)
GENERIC_CLUB_WORDS = {"football", "club", "futbol", "sport", "sports", "athletic", "calcio", "associazione",
                      "sportiva", "united", "city", "real", "union"}
WD_API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
IMG_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org", "commons.wikimedia.org"}
NOT_FOUND = {"found": False}

TEAMS_SPARQL = """
SELECT ?team ?teamLabel ?flag ?logo ?article WHERE {
  VALUES ?cls { wd:Q6979593 wd:Q135408445 }   # national team / men's national team
  ?team wdt:P31 ?cls .
  OPTIONAL { ?team wdt:P17 ?country . ?country wdt:P41 ?flag . ?country rdfs:label ?countryLabel . FILTER(lang(?countryLabel) = "en") }
  OPTIONAL { ?team wdt:P154 ?logo . }
  OPTIONAL { ?article schema:about ?team ; schema:isPartOf <https://en.wikipedia.org/> . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""
TEAM_SUFFIX = re.compile(r"\s+(men's\s+)?national\s+(association\s+)?(football|soccer)\s+team$", re.I)
NOT_SENIOR = re.compile(r"women|under-|\bu-?\d|youth|olympic|\bb team|amateur|futsal|beach|legends|universit", re.I)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


_api_lock = threading.Lock()
_last_call = [0.0]
# Seconds between Wikimedia API calls (serial, never parallel), kept under the applicable limit.
MIN_INTERVAL = 0.4 if CONTACT else 6.5


def _get_json(url: str, params: dict[str, Any], timeout: float = 20) -> Any:
    """GET JSON from a Wikimedia API: one call at a time, spaced out, backing off on 429."""
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers=HEADERS)
    for attempt in range(4):
        with _api_lock:
            wait = _last_call[0] + MIN_INTERVAL - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 3:
                    raise
                retry = e.headers.get("Retry-After", "")
                time.sleep(min(30, int(retry)) if retry.isdigit() else 5 * (attempt + 1))  # their guidance: >= 5 s
            finally:
                _last_call[0] = time.time()
    return None


def commons_thumb(file_url_or_name: str, width: int) -> str:
    """Special:FilePath URL that redirects to a PNG/JPG thumbnail of a Commons file."""
    name = file_url_or_name.rsplit("/Special:FilePath/", 1)[-1]
    name = urllib.parse.unquote(name)
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(name)}?width={width}"


def proxied(url: str | None) -> str | None:
    return f"/api/img?u={urllib.parse.quote(url, safe='')}" if url else None


def _year(t: str | None) -> int | None:
    m = re.match(r"[+-]?(\d{4})", t or "")
    return int(m.group(1)) if m else None


class Media:
    def __init__(self, teams: list[dict[str, str]]) -> None:
        """teams: [{"id": "T:brazil", "name": "Brazil"}, ...] from the Store."""
        CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(CACHE_DB, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, data TEXT, fetched REAL)")
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}
        # Player lookups run in one background worker; user requests jump ahead of the warm-up.
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._queued: set[str] = set()
        self._seq = itertools.count()
        threading.Thread(target=self._worker, daemon=True).start()
        self.teams = teams
        self._teams_ready = threading.Event()
        self.clubs: list[dict[str, Any]] = []   # [{"id": "CL:fc_barcelona", "tm": 131}], set by the app
        self._clubs_ready = threading.Event()

    # ------------------------------------------------------------------ cache
    def _get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM media WHERE id = ?", [key]).fetchone()
        return json.loads(row[0]) if row else None

    def _put(self, key: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO media VALUES (?, ?, ?)", [key, json.dumps(data), time.time()])
            self._db.commit()

    def _once(self, key: str, fetch) -> dict[str, Any]:
        """Return the cached value, or fetch it exactly once even under concurrent requests."""
        cached = self._get(key)
        if cached is not None:
            return cached
        with self._lock:
            ev = self._inflight.get(key)
            owner = ev is None
            if owner:
                ev = self._inflight[key] = threading.Event()
        if not owner:
            ev.wait(30)
            return self._get(key) or NOT_FOUND
        try:
            try:
                data = fetch()
            except Exception:  # noqa: BLE001 - network hiccup: don't cache, try again next time
                return NOT_FOUND
            self._put(key, data)
            return data
        finally:
            ev.set()
            with self._lock:
                self._inflight.pop(key, None)

    # ------------------------------------------------------------------ teams (bulk)
    def warm_teams(self) -> None:
        """One SPARQL query resolves every national team's flag and crest; cached for good."""
        try:
            if self._get("__teams__") is None:
                rows = _get_json(SPARQL, {"query": TEAMS_SPARQL, "format": "json"}, timeout=60)["results"]["bindings"]
                by_name: dict[str, dict[str, Any]] = {}
                for b in rows:
                    label = b.get("teamLabel", {}).get("value", "")
                    if NOT_SENIOR.search(label) or not TEAM_SUFFIX.search(label):
                        continue
                    key = _norm(TEAM_SUFFIX.sub("", label))
                    cur = by_name.setdefault(key, {"qid": b["team"]["value"].rsplit("/", 1)[-1]})
                    if "flag" in b and "flag" not in cur:
                        cur["flag"] = b["flag"]["value"]
                        cur["country"] = b.get("countryLabel", {}).get("value", "")
                    if "logo" in b and "crest" not in cur:
                        cur["crest"] = b["logo"]["value"]
                    if "article" in b and "wiki" not in cur:
                        cur["wiki"] = b["article"]["value"]
                found: dict[str, dict[str, Any]] = {}
                for t in self.teams:
                    hit = by_name.get(_norm(t["name"])) or by_name.get(_norm(ALIASES.get(t["name"], "")))
                    if hit:
                        found[t["id"]] = dict(hit)
                # Bigger teams Wikidata typed differently: look them up through Wikipedia.
                for t in sorted(self.teams, key=lambda t: -t.get("matches", 0)):
                    if t["id"] not in found and t.get("matches", 0) >= 100:
                        hit = self._team_via_wikipedia(ALIASES.get(t["name"], t["name"]))
                        if hit:
                            found[t["id"]] = hit
                # Territories and home nations: Wikidata's country is the sovereign state (England -> UK),
                # so take the flag of the place named like the team instead.
                names = {t["id"]: ALIASES.get(t["name"], t["name"]) for t in self.teams}
                own = [tid for tid, h in found.items() if _norm(h.get("country", names[tid])) != _norm(names[tid])]
                own += [t["id"] for t in self.teams if t["id"] not in found]
                flags = self._flags_by_place_name({names[tid] for tid in own})
                for tid in own:
                    f = flags.get(_norm(names[tid]))
                    if f:
                        found.setdefault(tid, {"qid": None})["flag"] = f
                    elif tid in found and _norm(found[tid].get("country", "")) != _norm(names[tid]):
                        found[tid].pop("flag", None)  # better no flag than the wrong one
                for tid, hit in found.items():
                    self._put(tid, {"found": True, **hit})
                matched = len(found)
                self._put("__teams__", {"matched": matched, "total": len(self.teams)})
        except Exception:  # noqa: BLE001 - offline: the app works without media
            pass
        finally:
            self._teams_ready.set()

    def _team_via_wikipedia(self, name: str) -> dict[str, Any] | None:
        try:
            res = _get_json(WIKI_API, {"action": "query", "format": "json", "redirects": 1,
                                       "titles": f"{name} national football team|{name} national soccer team",
                                       "prop": "pageprops|info", "ppprop": "wikibase_item", "inprop": "url"})
            page = next((p for p in res["query"]["pages"].values() if "pageprops" in p), None)
            if not page:
                return None
            qid = page["pageprops"]["wikibase_item"]
            claims = _get_json(WD_API, {"action": "wbgetentities", "ids": qid, "props": "claims",
                                        "format": "json"})["entities"][qid]["claims"]
            out: dict[str, Any] = {"qid": qid, "wiki": page.get("fullurl"), "country": ""}
            if claims.get("P154"):
                out["crest"] = claims["P154"][0]["mainsnak"]["datavalue"]["value"]
            return out
        except Exception:  # noqa: BLE001
            return None

    def _flags_by_place_name(self, names: set[str]) -> dict[str, str]:
        """Flags (P41) of places whose English label equals one of `names`."""
        if not names:
            return {}
        values = " ".join(json.dumps(n) + "@en" for n in names)
        q = f"""SELECT ?label ?flag WHERE {{ VALUES ?label {{ {values} }}
                 ?place rdfs:label ?label ; wdt:P41 ?flag . }}"""
        try:
            rows = _get_json(SPARQL, {"query": q, "format": "json"}, timeout=60)["results"]["bindings"]
        except Exception:  # noqa: BLE001
            return {}
        out: dict[str, str] = {}
        for b in rows:
            out.setdefault(_norm(b["label"]["value"]), b["flag"]["value"])
        return out

    @staticmethod
    def _flag_for(tid: str, m: dict[str, Any]) -> str | None:
        override = FLAG_FILES.get(tid)
        return override or m.get("flag")

    def team_images(self) -> dict[str, dict[str, str | None]]:
        """{team_id: {flag, crest}} with thumbnail URLs routed through the image proxy."""
        self._teams_ready.wait(70)
        out = {}
        for t in self.teams:
            m = self._get(t["id"]) or {}
            flag = self._flag_for(t["id"], m)
            if m.get("found") or flag:
                out[t["id"]] = {
                    "flag": proxied(commons_thumb(flag, 96)) if flag else None,
                    "crest": proxied(commons_thumb(m["crest"], 160)) if m.get("crest") else None,
                }
        return out

    def team_detail(self, tid: str, name: str) -> dict[str, Any]:
        base = self._get(tid) or {}

        def fetch() -> dict[str, Any]:
            title = (base.get("wiki") or "").rsplit("/wiki/", 1)[-1]
            title = urllib.parse.unquote(title) if title else f"{name} national football team"
            pages = _get_json(WIKI_API, {
                "action": "query", "format": "json", "titles": title, "redirects": 1,
                "prop": "description|extracts|info", "exintro": 1, "explaintext": 1, "exsentences": 2,
                "inprop": "url",
            })["query"]["pages"]
            page = next(iter(pages.values()))
            if "missing" in page:
                return NOT_FOUND
            return {"found": True, "description": page.get("description"), "extract": page.get("extract"),
                    "url": page.get("fullurl")}

        info = self._once(f"teamdetail:{tid}", fetch)
        return {
            **{k: v for k, v in info.items() if k != "found"},
            "flag": proxied(commons_thumb(f, 320)) if (f := self._flag_for(tid, base)) else None,
            "crest": proxied(commons_thumb(base["crest"], 240)) if base.get("crest") else None,
        }

    # ------------------------------------------------------------------ players (on demand)
    def _fetch_player(self, name: str, team: str) -> dict[str, Any]:
        props = {"format": "json", "action": "query", "prop": "pageimages|description|pageprops|info",
                 "piprop": "thumbnail", "pithumbsize": 400, "ppprop": "wikibase_item", "inprop": "url"}
        surname = _norm(name).split()[-1] if name.strip() else ""

        def ok(p: dict) -> bool:
            return (bool(re.search(r"football|soccer|futbol", p.get("description", ""), re.I))
                    and not re.match(r"(career|list|international career|statistics) of", p.get("title", ""), re.I)
                    and bool(surname) and surname in _norm(p.get("title", "")))

        # 1) the article titled exactly like the player (most famous players), then 2) a search
        exact = _get_json(WIKI_API, {**props, "titles": f"{name}|{name} (footballer)", "redirects": 1})
        pages = list((exact or {}).get("query", {}).get("pages", {}).values())
        page = next((p for p in pages if "missing" not in p and ok(p)), None)
        if not page:
            res = _get_json(WIKI_API, {**props, "generator": "search", "gsrsearch": f"{name} footballer {team}",
                                       "gsrlimit": 5})
            pages = sorted((res or {}).get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
            page = next((p for p in pages if ok(p)), None)
        if not page:
            return NOT_FOUND
        out: dict[str, Any] = {
            "found": True,
            "title": page["title"],
            "url": page.get("fullurl"),
            "description": page.get("description"),
            "photo": page.get("thumbnail", {}).get("source"),
        }
        qid = page.get("pageprops", {}).get("wikibase_item")
        if qid:
            out.update(self._wikidata_career(qid))
        return out

    def _wikidata_career(self, qid: str) -> dict[str, Any]:
        ent = _get_json(WD_API, {"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"})
        claims = ent["entities"][qid].get("claims", {})

        def val(c: dict) -> Any:
            return c.get("mainsnak", {}).get("datavalue", {}).get("value")

        born = _year((val(claims["P569"][0]) or {}).get("time")) if claims.get("P569") else None
        positions = [v["id"] for c in claims.get("P413", []) if (v := val(c)) and "id" in v][:2]
        spells = []
        for c in claims.get("P54", []):
            v = val(c)
            if not v or "id" not in v:
                continue
            q = c.get("qualifiers", {})
            start = _year(q["P580"][0]["datavalue"]["value"]["time"]) if q.get("P580") and "datavalue" in q["P580"][0] else None
            end = _year(q["P582"][0]["datavalue"]["value"]["time"]) if q.get("P582") and "datavalue" in q["P582"][0] else None
            spells.append({"qid": v["id"], "start": start, "end": end})
        ids = list(dict.fromkeys([s["qid"] for s in spells] + positions))[:50]
        labels, logos = {}, {}
        if ids:
            ents = _get_json(WD_API, {"action": "wbgetentities", "ids": "|".join(ids), "props": "labels|claims",
                                      "languages": "en", "format": "json"})["entities"]
            for i, e in ents.items():
                labels[i] = e.get("labels", {}).get("en", {}).get("value")
                logo = e.get("claims", {}).get("P154")
                if logo:
                    logos[i] = val(logo[0])
        clubs = [
            {"name": labels.get(s["qid"]), "start": s["start"], "end": s["end"],
             "logo": commons_thumb(logos[s["qid"]], 96) if logos.get(s["qid"]) else None}
            for s in spells
            if labels.get(s["qid"]) and not re.search(r"national|olympic|under-\d", labels[s["qid"]], re.I)
        ]
        seen, unique = set(), []
        for c in sorted(clubs, key=lambda c: (c["start"] or 9999)):
            if c["name"] not in seen:
                seen.add(c["name"])
                unique.append(c)
        clubs = unique
        return {"born": born, "position": ", ".join(labels[p] for p in positions if labels.get(p)) or None,
                "clubs": clubs}

    def player(self, pid: str, name: str, team: str) -> dict[str, Any]:
        return self._once(pid, lambda: self._fetch_player(name, team))

    def _worker(self) -> None:
        while True:
            _, _, item = self._queue.get()
            try:
                self.player(item["id"], item["name"], item["team"])
            finally:
                with self._lock:
                    self._queued.discard(item["id"])

    def enqueue(self, items: list[dict[str, str]], priority: int = 0) -> None:
        for i in items:
            with self._lock:
                if i["id"] in self._queued:
                    continue
                self._queued.add(i["id"])
            self._queue.put((priority, next(self._seq), i))

    def players(self, items: list[dict[str, str]]) -> dict[str, Any]:
        """Photos for players already looked up; the rest are queued and reported as pending."""
        photos: dict[str, str | None] = {}
        todo = []
        for i in items[:60]:
            m = self._get(i["id"])
            if m is None:
                todo.append(i)
            else:
                photos[i["id"]] = proxied(m.get("photo")) if m.get("found") else None
        self.enqueue(todo, priority=0)
        return {"photos": photos, "pending": [i["id"] for i in todo]}

    # ------------------------------------------------------------------ clubs (bulk)
    def warm_clubs(self) -> None:
        """Club logos, matched exactly by Transfermarkt ID (Wikidata P7223), never by name.

        1. Wikidata: the free logo on Commons (P154) and the club's English Wikipedia article, in a few SPARQL
           queries.
        2. Clubs without a free logo: the main image of their Wikipedia article, which for a club is its crest
           (often a non-free file hosted on en.wikipedia under fair use). 50 articles per API call; only images
           that look like a crest (SVG/PNG, or "logo"/"crest"/"badge" in the file name) are kept, so a stadium
           photo never shows up as a logo.
        All calls are serial and throttled like every other Wikimedia call; results are cached for good."""
        try:
            if self.clubs and self._get("__clubs_v2__") is None:
                by_tm = {str(c["tm"]): c["id"] for c in self.clubs if c.get("tm") is not None}
                ids = sorted(by_tm)
                articles: dict[str, str] = {}   # enwiki title -> club id (clubs still without a logo)
                for i in range(0, len(ids), 300):
                    values = " ".join(f'"{x}"' for x in ids[i:i + 300])
                    q = (f"SELECT ?tm ?club ?logo ?article WHERE {{ VALUES ?tm {{ {values} }} ?club wdt:P7223 ?tm . "
                         f"OPTIONAL {{ ?club wdt:P154 ?logo . }} "
                         f"OPTIONAL {{ ?article schema:about ?club ; schema:isPartOf <https://en.wikipedia.org/> . }} }}")
                    rows = _get_json(SPARQL, {"query": q, "format": "json"}, timeout=60)["results"]["bindings"]
                    for b in rows:
                        cid = by_tm.get(b["tm"]["value"])
                        if not cid:
                            continue
                        if "logo" in b:
                            if not (self._get(cid) or {}).get("logo"):
                                self._put(cid, {"found": True, "logo": b["logo"]["value"],
                                                "qid": b["club"]["value"].rsplit("/", 1)[-1]})
                        elif "article" in b:
                            title = urllib.parse.unquote(b["article"]["value"].rsplit("/wiki/", 1)[-1]).replace("_", " ")
                            articles[title] = cid
                todo = [t for t, cid in articles.items() if not (self._get(cid) or {}).get("logo")]
                for i in range(0, len(todo), 50):
                    batch = todo[i:i + 50]
                    r = _get_json(WIKI_API, {"action": "query", "format": "json", "formatversion": 2,
                                             "prop": "pageimages", "piprop": "thumbnail|name", "pithumbsize": 120,
                                             "pilicense": "any",  # crests are usually non-free files
                                             "redirects": 1, "titles": "|".join(batch)})
                    q = r.get("query", {})
                    back = {x["to"]: x["from"] for x in q.get("redirects", [])}
                    back.update({x["to"]: x["from"] for x in q.get("normalized", [])})
                    for page in q.get("pages", []):
                        thumb, name = page.get("thumbnail", {}).get("source"), page.get("pageimage", "")
                        title = page.get("title", "")
                        cid = articles.get(title) or articles.get(back.get(title, ""), None) \
                            or articles.get(back.get(back.get(title, ""), ""), None)
                        if cid and thumb and CREST_FILE.search(name):
                            self._put(cid, {"found": True, "thumb": thumb, "source": "enwiki", "file": name})
                # 3. Still nothing (no Transfermarkt ID on Wikidata, or the article's image isn't a crest): search
                # Wikipedia for the club name. Only a few clubs, one call each; the hit must share a word with the
                # club's name and its image must look like a crest.
                for c in self.clubs:
                    if self._club_logo(c["id"]) or not c.get("name"):
                        continue
                    name = re.sub(r"\(.*?\)", "", c["name"]).strip()
                    r = _get_json(WIKI_API, {"action": "query", "format": "json", "formatversion": 2,
                                             "generator": "search", "gsrsearch": f"{name} football club",
                                             "gsrlimit": 3, "prop": "pageimages", "piprop": "thumbnail|name",
                                             "pithumbsize": 120, "pilicense": "any"})
                    # the club's first distinctive word must be in the article title ("Metalist Kharkiv" must
                    # not match "FC Kharkiv", a different club)
                    words = [w for w in re.findall(r"\w+", _norm(name)) if len(w) > 3 and w not in GENERIC_CLUB_WORDS]
                    pages = sorted(r.get("query", {}).get("pages", []), key=lambda pg: pg.get("index", 99))
                    for page in pages:
                        thumb, file = page.get("thumbnail", {}).get("source"), page.get("pageimage", "")
                        if thumb and CREST_FILE.search(file) and words \
                                and words[0] in re.findall(r"\w+", _norm(page.get("title", ""))):
                            self._put(c["id"], {"found": True, "thumb": thumb, "source": "enwiki-search", "file": file})
                            break
                found = sum(1 for c in self.clubs if self._club_logo(c["id"]))
                self._put("__clubs_v2__", {"matched": found, "total": len(ids)})
        except Exception:  # noqa: BLE001 - offline: the app works without logos
            pass
        finally:
            self._clubs_ready.set()

    def _club_logo(self, cid: str) -> str | None:
        m = self._get(cid) or {}
        if m.get("logo"):
            return proxied(commons_thumb(m["logo"], 96))
        if m.get("thumb"):
            return proxied(m["thumb"])
        return None

    def warm_all(self) -> None:
        self.warm_teams()
        self.warm_clubs()

    def club_images(self) -> dict[str, str]:
        """{club_id: logo thumbnail URL via the image proxy}."""
        out = {}
        for c in self.clubs:
            logo = self._club_logo(c["id"])
            if logo:
                out[c["id"]] = logo
        return out

    @staticmethod
    def player_public(m: dict[str, Any]) -> dict[str, Any] | None:
        if not m.get("found"):
            return None
        return {
            "title": m.get("title"), "url": m.get("url"), "description": m.get("description"),
            "photo": proxied(m.get("photo")), "born": m.get("born"), "position": m.get("position"),
            "clubs": [{**c, "logo": proxied(c["logo"])} for c in m.get("clubs", [])],
        }

    # ------------------------------------------------------------------ image proxy
    def image_file(self, url: str) -> Path | None:
        """Download (once) and return the local file for an allowed Wikimedia image URL."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in IMG_HOSTS:
            return None
        h = hashlib.sha1(url.encode()).hexdigest()
        existing = next(IMG_DIR.glob(f"{h}.*"), None)
        if existing:
            return existing
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                final_host = urllib.parse.urlparse(r.geturl()).hostname
                ctype = r.headers.get("Content-Type", "")
                if final_host not in IMG_HOSTS or not ctype.startswith("image/"):
                    return None
                data = r.read(4_000_000)
        except Exception:  # noqa: BLE001
            return None
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/svg+xml": "svg", "image/webp": "webp",
               "image/gif": "gif"}.get(ctype.split(";")[0], "img")
        path = IMG_DIR / f"{h}.{ext}"
        path.write_bytes(data)
        return path


# Dataset names that differ from Wikidata's labels.
ALIASES = {
    "DR Congo": "Democratic Republic of the Congo",
    "United States": "United States",
    "China PR": "China",
    "Ivory Coast": "Ivory Coast",
    "Republic of Ireland": "Republic of Ireland",
    "North Macedonia": "North Macedonia",
    "Eswatini": "Eswatini",
    "Taiwan": "Chinese Taipei",
    "Vietnam": "Vietnam",
    "Kyrgyzstan": "Kyrgyzstan",
    "Brunei": "Brunei",
    "Timor-Leste": "Timor-Leste",
    "Cape Verde": "Cape Verde",
    "São Tomé and Príncipe": "São Tomé and Príncipe",
    "Saint Kitts and Nevis": "Saint Kitts and Nevis",
    "United States Virgin Islands": "U.S. Virgin Islands",
    "German DR": "East Germany",
    "Vietnam Republic": "South Vietnam",
    "Yemen DPR": "South Yemen",
    "Czech Republic": "Czech Republic",
    "Gambia": "The Gambia",
}


# Flags Wikidata can't resolve by name (Commons file names).
FLAG_FILES = {
    "T:northern_ireland": "Ulster Banner.svg",
    "T:republic_of_ireland": "Flag of Ireland.svg",
    "T:congo": "Flag of the Republic of the Congo.svg",
    "T:taiwan": "Flag of the Republic of China.svg",
    "T:bahamas": "Flag of the Bahamas.svg",
    "T:gibraltar": "Flag of Gibraltar.svg",
    "T:united_states_virgin_islands": "Flag of the United States Virgin Islands.svg",
    "T:zanzibar": "Flag of Zanzibar.svg",
    "T:vietnam_republic": "Flag of South Vietnam.svg",
    "T:åland_islands": "Flag of Åland.svg",
    "T:shetland": "Flag of Shetland.svg",
    "T:orkney": "Flag of Orkney.svg",
    "T:abkhazia": "Flag of the Republic of Abkhazia.svg",
}
