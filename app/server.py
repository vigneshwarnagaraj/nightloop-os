"""NIGHTLOOP - V1 browser prototype: local server (v0.3).

Runs on http://localhost:8080 using only Python's standard library.
- Serves the map page (static/).
- Traffic from four sources, converted into one shared format:
  AI planes (worldwide, clock-synced simulation), VATSIM, IVAO, real-world ADS-B
  (airplanes.live, adsb.lol, adsb.fi and, optionally, a local FreeFlight API aggregator).
- Downloads and caches OurAirports data (airports + runways).
- Looks up flight routes.
Close the window to stop it. AI flights are saved in data/ai_state.json and keep flying
with the real clock while NIGHTLOOP is closed, unless you pause them.
"""
import collections, csv, json, math, os, random, re, shutil, subprocess, sys, threading, time, urllib.error, urllib.request, webbrowser
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
from urllib.parse import parse_qs, urlparse

APP, VERSION = "NIGHTLOOP", "1.5"
ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
DATA = os.path.join(ROOT, "data")
CONFIG_PATH = os.path.join(ROOT, "config.json")
AI_STATE_PATH = os.path.join(DATA, "ai_state.json")
UA = f"NIGHTLOOP-V1-prototype/{VERSION} (hobby, non-commercial)"
os.makedirs(DATA, exist_ok=True)

DEFAULT_CONFIG = {
    "port": 8080,
    "home": {"icao": "KORD", "lat": 41.9786, "lon": -87.9048},
    "sources": {"ai": True, "vatsim": True, "ivao": True, "real": True},
    "ivao_api_key": "",
    "real_providers": {"airplanes.live": True, "adsb.lol": True, "adsb.fi": True, "freeflight": False},
    "freeflight_url": "http://localhost:8000",
    "freeflight_autostart": True,
    "ai_flights": 300,
    "ai_home_share": 0.1,
    "tower": {"enabled": True, "arr_rwy": "", "dep_rwy": ""},
    "center": {"enabled": True, "radius": 200},
    "ground": {"enabled": True},
    # positions YOU work; AI controllers handle the others
    "positions": {"ground": True, "tower": True, "approach": False, "center": False},
    "lan": True,                 # reachable from other devices on the home network (Pi, updates)
    "update_source": "",         # Windows app: folder with newer NIGHTLOOP files (e.g. D:\\ATC SIM\\V1\\web_prototype)
}


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    user = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception as e:
            log(f"config.json could not be read ({e}); using defaults")
    cfg.pop("real_world_provider", None)          # replaced by real_providers in v0.3
    if cfg.get("ai_flights") == 14 and "ai_home_share" not in user:
        cfg["ai_flights"] = 300                    # v0.2 default (14 local) -> v0.3 default (300 worldwide)
    return cfg


def save_config():
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(CFG, f, indent=2)



CFG = load_config()
VIEW = {"t": 0.0, "lat": CFG["home"]["lat"], "lon": CFG["home"]["lon"], "z": 7.0, "centers": []}


def http_json(url, headers=None, timeout=20, data=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=h, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def iso_ts(s, default):
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return default


def target(id_, src, cs, typ, lat, lon, alt, gs, trk, vs=0, sq="", dep="", arr="", ground=False, ts=None, **extra):
    """The one shared aircraft format every source is converted into. ts = time of the position (unix s)."""
    if lat is None or lon is None:
        return None
    t = {
        "id": str(id_), "source": src, "callsign": (cs or "").strip() or str(id_),
        "type": typ or "", "lat": round(num(lat), 5), "lon": round(num(lon), 5),
        "alt": int(num(alt)), "gs": int(num(gs)), "track": round(num(trk), 1),
        "vs": int(num(vs)), "squawk": str(sq or ""), "dep": (dep or "").upper(), "arr": (arr or "").upper(),
        "ground": bool(ground), "ts": round(ts or time.time(), 2),
    }
    t.update(extra)
    return t


def dist_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3440.065 * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def gc_point(lat1, lon1, lat2, lon2, f):
    """Point at fraction f along the great circle from 1 to 2."""
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d = 2 * math.asin(min(1.0, math.sqrt(math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2)))
    if d < 1e-9:
        return lat1, lon1
    a, b = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
    x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
    y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
    z = a * math.sin(p1) + b * math.sin(p2)
    return math.degrees(math.atan2(z, math.sqrt(x * x + y * y))), math.degrees(math.atan2(y, x))


def in_lon(lon, w, e):
    return w <= lon <= e if w <= e else (lon >= w or lon <= e)


# ---------------------------------------------------------------- airports

AIRPORTS, AP_INDEX, RUNWAYS, LARGE, RWY_ENDS = [], {}, {}, [], {}
AP_STATE = {"ready": False, "error": ""}
AP_TYPES = ("large_airport", "medium_airport", "small_airport")
OURAIRPORTS = "https://davidmegginson.github.io/ourairports-data/"


FORCE_DL = {"on": False}


def load_airports():
    AIRPORTS.clear(); AP_INDEX.clear(); RUNWAYS.clear(); LARGE.clear(); RWY_ENDS.clear()
    try:
        for name in ("airports.csv", "runways.csv"):
            path = os.path.join(DATA, name)
            if FORCE_DL["on"] or not os.path.exists(path) or time.time() - os.path.getmtime(path) > 7 * 86400:
                try:
                    log(f"Downloading {name} from OurAirports...")
                    req = urllib.request.Request(OURAIRPORTS + name, headers={"User-Agent": UA})
                    with urllib.request.urlopen(req, timeout=180) as r:
                        blob = r.read()
                    with open(path + ".tmp", "wb") as f:
                        f.write(blob)
                    os.replace(path + ".tmp", path)
                except Exception as e:
                    if not os.path.exists(path):
                        raise
                    log(f"Could not refresh {name} ({e}); using the saved copy")
        with open(os.path.join(DATA, "airports.csv"), encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("type") not in AP_TYPES:
                    continue
                try:
                    lat, lon = float(row["latitude_deg"]), float(row["longitude_deg"])
                except (TypeError, ValueError):
                    continue
                code = row.get("icao_code") or row.get("gps_code") or row["ident"]
                a = {"code": code, "ident": row["ident"], "name": row.get("name", ""), "type": row["type"],
                     "lat": round(lat, 5), "lon": round(lon, 5), "elev": row.get("elevation_ft", ""),
                     "city": row.get("municipality", ""), "iata": row.get("iata_code", ""),
                     "sched": row.get("scheduled_service", "") == "yes"}
                AIRPORTS.append(a)
                if row["type"] == "large_airport" and a["sched"]:
                    LARGE.append(a)
                for k in (row["ident"], row.get("icao_code", ""), row.get("gps_code", "")):
                    if k:
                        AP_INDEX.setdefault(k.upper(), a)
        for a in AIRPORTS:
            if a["iata"]:
                AP_INDEX.setdefault(a["iata"].upper(), a)
        with open(os.path.join(DATA, "runways.csv"), encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("closed") == "1":
                    continue
                try:
                    seg = [round(float(row["le_latitude_deg"]), 5), round(float(row["le_longitude_deg"]), 5),
                           round(float(row["he_latitude_deg"]), 5), round(float(row["he_longitude_deg"]), 5),
                           (row.get("le_ident") or "").strip().upper(), (row.get("he_ident") or "").strip().upper()]
                except (TypeError, ValueError, KeyError):
                    continue
                RUNWAYS.setdefault(row["airport_ident"], []).append(seg)
                ap = AP_INDEX.get(row["airport_ident"].upper())
                ap_elev = num(ap["elev"]) if ap else 0.0
                for e, o, a1, a2 in (("le", "he", seg[0:2], seg[2:4]), ("he", "le", seg[2:4], seg[0:2])):
                    rid = (row.get(e + "_ident") or "").strip().upper()
                    if not rid:
                        continue
                    RWY_ENDS.setdefault(row["airport_ident"], []).append({
                        "id": rid, "lat": a1[0], "lon": a1[1],
                        "crs": num(row.get(e + "_heading_degT"), bearing(a1[0], a1[1], a2[0], a2[1])),
                        "elev": num(row.get(e + "_elevation_ft"), ap_elev),
                        "pair": (row.get(o + "_ident") or "").strip().upper(), "len": num(row.get("length_ft"), 0)})
        AIRPORTS.sort(key=lambda a: AP_TYPES.index(a["type"]))
        load_navaids()
        AP_STATE["ready"] = True
        log(f"Airports ready: {len(AIRPORTS)} airports ({len(LARGE)} large hubs), runways for {len(RUNWAYS)} airports")
        AI.start()
    except Exception as e:
        AP_STATE["error"] = str(e)[:200]
        log(f"Airport data failed: {e} - retrying in 60 s")


NAVAIDS = []          # VOR / VORTAC / NDB ... from OurAirports navaids.csv
NAV_INDEX = {}        # ident -> [navaid, ...] (idents repeat around the world)
NAV_TYPES = {"VOR": "VOR", "VOR-DME": "VOR", "VORTAC": "VOR", "TACAN": "VOR", "DME": "DME", "NDB": "NDB", "NDB-DME": "NDB"}


def load_navaids():
    """Optional: radio navigation beacons for 'direct to' and the Center/Approach maps. A failure never stops the simulator."""
    NAVAIDS.clear(); NAV_INDEX.clear()
    path = os.path.join(DATA, "navaids.csv")
    try:
        if FORCE_DL["on"] or not os.path.exists(path) or time.time() - os.path.getmtime(path) > 7 * 86400:
            try:
                log("Downloading navaids.csv from OurAirports...")
                req = urllib.request.Request(OURAIRPORTS + "navaids.csv", headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=180) as r:
                    blob = r.read()
                with open(path + ".tmp", "wb") as f:
                    f.write(blob)
                os.replace(path + ".tmp", path)
            except Exception as e:
                if not os.path.exists(path):
                    log(f"Navaids not available ({e}) - direct-to works with airports only")
                    return
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                kind = NAV_TYPES.get(row.get("type", ""))
                if not kind or kind == "DME":
                    continue
                try:
                    lat, lon = float(row["latitude_deg"]), float(row["longitude_deg"])
                except (TypeError, ValueError, KeyError):
                    continue
                khz = num(row.get("frequency_khz"), 0)
                n = {"id": (row.get("ident") or "").strip().upper(), "name": row.get("name", "").title(), "kind": kind,
                     "lat": round(lat, 5), "lon": round(lon, 5), "freq": (f"{khz / 1000:.2f}" if kind == "VOR" else f"{khz:.0f}") if khz else ""}
                if not n["id"]:
                    continue
                NAVAIDS.append(n)
                NAV_INDEX.setdefault(n["id"], []).append(n)
        log(f"Navaids ready: {len(NAVAIDS)} VOR/NDB beacons")
    except Exception as e:
        log(f"Navaids failed ({e}) - direct-to works with airports only")


def find_point(code, lat, lon):
    """Airport (ICAO/IATA) or the nearest navaid with that ident within 600 NM."""
    a = AP_INDEX.get(code)
    if a:
        return {"code": a["code"], "lat": a["lat"], "lon": a["lon"], "kind": "airport"}
    best = None
    for n in NAV_INDEX.get(code, []):
        d = dist_nm(lat, lon, n["lat"], n["lon"])
        if d < 600 and (not best or d < best[0]):
            best = (d, n)
    if best:
        n = best[1]
        return {"code": n["id"], "lat": n["lat"], "lon": n["lon"], "kind": n["kind"]}
    return None


def api_navaids(q):
    s, w, n, e, z = parse_box(q)
    if z < 6 or not NAVAIDS:
        return {"navaids": []}
    kinds = ("VOR",) if z < 8 else ("VOR", "NDB")
    out = [x for x in NAVAIDS if x["kind"] in kinds and s <= x["lat"] <= n and in_lon(x["lon"], w, e)]
    return {"navaids": out[:1500]}


def airport_loader():
    while not AP_STATE["ready"]:
        load_airports()
        if not AP_STATE["ready"]:
            time.sleep(60)


def airport_brief(code, fallback=None):
    a = AP_INDEX.get((code or "").strip().upper())
    if a:
        return {"code": a["code"], "name": a["name"], "city": a["city"], "lat": a["lat"], "lon": a["lon"]}
    if fallback and fallback.get("lat") is not None:
        return {"code": code, "name": fallback.get("name", ""), "city": "", "lat": num(fallback["lat"]),
                "lon": num(fallback.get("lon", fallback.get("lng")))}
    return None


# ---------------------------------------------------------------- feeds

class Feed:
    name = "feed"
    interval = 15

    def __init__(self):
        self.targets, self.updated, self.error, self.note, self.backoff = [], 0.0, "", "", 0
        self.wake = threading.Event()

    def status(self):
        return {"name": self.name, "enabled": bool(CFG["sources"].get(self.name, True)),
                "count": len(self.targets), "error": self.error, "note": self.note,
                "age": round(time.time() - self.updated, 1) if self.updated else None,
                "interval": self.interval}

    def fetch(self):
        raise NotImplementedError

    def run(self):
        while True:
            idle = self.name != "ai" and time.time() - VIEW["t"] > 60
            if CFG["sources"].get(self.name, True) and not idle:
                try:
                    self.targets = [t for t in self.fetch() if t]
                    self.updated, self.error, self.backoff = time.time(), "", 0
                except urllib.error.HTTPError as e:
                    self.error = f"HTTP {e.code}" + (" - add ivao_api_key in config.json" if e.code in (401, 403) and self.name == "ivao" else "")
                    self.backoff = min(max(self.backoff * 2, 30 if e.code == 429 else self.interval), 600)
                    log(f"{self.name}: {self.error}")
                except Exception as e:
                    self.error = str(e)[:140]
                    self.backoff = min(max(self.backoff * 2, self.interval), 300)
                    log(f"{self.name}: {self.error}")
            self.wake.wait(self.interval + self.backoff if not idle else 2)
            self.wake.clear()


class VatsimFeed(Feed):
    name, interval = "vatsim", 15

    def __init__(self):
        super().__init__()
        self.url, self.url_t = None, 0.0

    def fetch(self):
        if not self.url or time.time() - self.url_t > 3600:
            st = http_json("https://status.vatsim.net/status.json")
            self.url, self.url_t = random.choice(st["data"]["v3"]), time.time()
        d = http_json(self.url)
        now, out = time.time(), []
        for p in d.get("pilots", []):
            try:
                fp = p.get("flight_plan") or {}
                out.append(target("vatsim-%s" % p.get("cid"), "vatsim", p.get("callsign"), fp.get("aircraft_short"),
                                  p.get("latitude"), p.get("longitude"), p.get("altitude"), p.get("groundspeed"),
                                  p.get("heading"), 0, p.get("transponder"), fp.get("departure"), fp.get("arrival"),
                                  num(p.get("groundspeed")) < 40, min(now, iso_ts(p.get("last_updated"), now))))
            except Exception:
                continue
        return out


class IvaoFeed(Feed):
    name, interval = "ivao", 15

    def fetch(self):
        key = CFG.get("ivao_api_key", "").strip()
        d = http_json("https://api.ivao.aero/v2/tracker/whazzup", {"apiKey": key} if key else None)
        now, out = time.time(), []
        for p in (d.get("clients") or {}).get("pilots", []):
            try:
                lt = p.get("lastTrack") or {}
                fp = p.get("flightPlan") or {}
                ac = fp.get("aircraft") or {}
                out.append(target("ivao-%s" % p.get("userId", p.get("callsign")), "ivao", p.get("callsign"),
                                  ac.get("icaoCode") or fp.get("aircraftId"), lt.get("latitude"), lt.get("longitude"),
                                  lt.get("altitude"), lt.get("groundSpeed"), lt.get("heading"), 0,
                                  lt.get("transponder"), fp.get("departureId"), fp.get("arrivalId"), lt.get("onGround"),
                                  min(now, iso_ts(lt.get("timestamp"), now))))
            except Exception:
                continue
        return out


def parse_adsb(d, t0):
    """Parse ADSBexchange-v2-style ('ac') or FreeFlight-style ('aircraft') responses into targets."""
    out = []
    rows = d.get("ac") if isinstance(d, dict) else None
    if rows is None and isinstance(d, dict):
        rows = d.get("aircraft") or d.get("states") or []
    for a in rows or []:
        try:
            if not isinstance(a, dict):
                continue
            alt = a.get("alt_baro", a.get("altitude_ft", a.get("alt_geom", 0)))
            ground = alt == "ground" or bool(a.get("on_ground"))
            seen = num(a.get("seen_pos", a.get("seen_pos_s", a.get("seen", 0))))
            t = target("real-%s" % str(a.get("hex", "")).lower(), "real",
                       a.get("flight") or a.get("callsign") or a.get("r") or a.get("registration") or a.get("hex"),
                       a.get("t") or a.get("type"), a.get("lat"), a.get("lon"), 0 if alt == "ground" else alt,
                       a.get("gs", a.get("ground_speed_kt")), a.get("track", a.get("true_heading", 0)),
                       a.get("baro_rate", a.get("vertical_rate_fpm", 0)), a.get("squawk"), "", "", ground, t0 - seen,
                       reg=a.get("r") or a.get("registration") or "")
            if t and a.get("hex"):
                out.append(t)
        except Exception:
            continue
    return out


def freeflight_exe():
    cand = [shutil.which("freeflight"),
            os.path.join(os.path.dirname(sys.executable), "Scripts", "freeflight.exe"),
            os.path.join(os.path.dirname(sys.executable), "freeflight")]
    return next((c for c in cand if c and os.path.exists(c)), None)


FREEFLIGHT_PROC = {"p": None}


def start_freeflight():
    if FREEFLIGHT_PROC["p"] and FREEFLIGHT_PROC["p"].poll() is None:
        return "already running"
    exe = freeflight_exe()
    if not exe:
        return "not installed - run INSTALL_FREEFLIGHT.bat once"
    try:
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        FREEFLIGHT_PROC["p"] = subprocess.Popen([exe, "serve"], creationflags=flags)
        log(f"Started FreeFlight API aggregator ({exe})")
        return "starting"
    except Exception as e:
        return f"could not start: {e}"


class RealFeed(Feed):
    """Real-world ADS-B. Enabled providers take turns (one request every 4 s in total). Each request covers 250 NM
    around the view centre. Aircraft are cached for 90 s so counts do not drop while panning. A provider answering
    HTTP 429 (rate limited) rests 2 minutes. FreeFlight (local aggregator) also provides a worldwide snapshot when
    you zoom out."""
    name, interval = "real", 4
    PROVIDERS = {
        "airplanes.live": "https://api.airplanes.live/v2/point/{lat:.2f}/{lon:.2f}/250",
        "adsb.lol": "https://api.adsb.lol/v2/point/{lat:.2f}/{lon:.2f}/250",
        "adsb.fi": "https://opendata.adsb.fi/api/v2/lat/{lat:.2f}/lon/{lon:.2f}/dist/250",
        "freeflight": "{base}/v2/point/{lat:.2f}/{lon:.2f}/250",
    }

    def __init__(self):
        super().__init__()
        self.cache, self.cool, self.turn, self.world_t = {}, {}, 0, 0.0
        self.pstat = {}

    def status(self):
        s = super().status()
        s["providers"] = {p: dict(self.pstat.get(p, {}), enabled=bool(CFG["real_providers"].get(p)),
                                  cooling=max(0, int(self.cool.get(p, 0) - time.time())))
                          for p in self.PROVIDERS}
        return s

    def _get(self, prov, url):
        t1 = time.time()
        try:
            d = http_json(url, timeout=15)
            self.pstat[prov] = {"ok": True, "last": time.strftime("%H:%M:%S"), "ms": int((time.time() - t1) * 1000), "msg": ""}
            return d
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self.cool[prov] = time.time() + 120
            self.pstat[prov] = {"ok": False, "last": time.strftime("%H:%M:%S"), "msg": f"HTTP {e.code}" + (" (rate limited, resting 2 min)" if e.code == 429 else "")}
        except Exception as e:
            self.cool[prov] = time.time() + 30
            self.pstat[prov] = {"ok": False, "last": time.strftime("%H:%M:%S"), "msg": str(e)[:80]}
        return None

    def fetch(self):
        now = time.time()
        enabled = [p for p in self.PROVIDERS if CFG["real_providers"].get(p)]
        if not enabled:
            self.note = "No real-world provider selected"
            return self._prune(now)
        base = CFG.get("freeflight_url", "http://localhost:8000").rstrip("/")
        if "freeflight" in enabled and VIEW["z"] < 5 and now - self.world_t > 30 and self.cool.get("freeflight", 0) <= now:
            self.world_t = now
            d = self._get("freeflight", base + "/v1/flights/world")
            if d is not None:
                n = 0
                for t in parse_adsb(d, time.time()):
                    self.cache[t["id"]] = t; n += 1
                self.note = f"World snapshot from FreeFlight: {n} aircraft"
                return self._prune(time.time())
        ready = [p for p in enabled if self.cool.get(p, 0) <= now]
        if not ready:
            self.note = "All selected providers are resting after errors or rate limits (showing cached aircraft)"
            return self._prune(now)
        prov = ready[self.turn % len(ready)]
        self.turn += 1
        cs_ = VIEW.get("centers") or [(VIEW["lat"], VIEW["lon"])]
        clat, clon = cs_[(self.turn // max(1, len(ready))) % len(cs_)]
        lat, lon = round(clat * 2) / 2, round(clon * 2) / 2
        d = self._get(prov, self.PROVIDERS[prov].format(lat=lat, lon=lon, base=base))
        if d is not None:
            for t in parse_adsb(d, time.time()):
                self.cache[t["id"]] = t
            self.note = f"Last query: {prov}, 250 NM around {lat:.1f}, {lon:.1f}"
        return self._prune(time.time())

    def _prune(self, now):
        self.cache = {k: v for k, v in self.cache.items() if now - v["ts"] < 90}
        return list(self.cache.values())


# ---------------------------------------------------------------- worldwide AI traffic

AIRLINES = ["AAL", "UAL", "DAL", "SWA", "JBU", "ASA", "ACA", "WJA", "AMX", "BAW", "DLH", "AFR", "KLM", "IBE", "RYR",
            "EZY", "THY", "UAE", "QTR", "ETD", "SIA", "CPA", "JAL", "ANA", "KAL", "CCA", "CES", "QFA", "ANZ", "AIC",
            "IGO", "ETH", "SAA", "LAN", "AZU", "GLO", "AVA", "SAS", "FIN", "TAP"]
# type, cruise ground speed (kt), max cruise altitude (ft), climb rate (fpm)
TYPES_SHORT = [("E175", 440, 37000, 2200), ("CRJ9", 450, 37000, 2200), ("AT76", 270, 25000, 1300), ("DH8D", 300, 25000, 1500)]
TYPES_MED = [("A320", 450, 39000, 2200), ("B738", 455, 41000, 2200), ("A321", 450, 39000, 2000), ("A20N", 450, 39000, 2300), ("B38M", 455, 41000, 2300)]
TYPES_LONG = [("B77W", 490, 41000, 1800), ("B789", 490, 43000, 2000), ("A359", 490, 43000, 2000), ("A333", 470, 41000, 1800), ("B744", 490, 41000, 1600)]


def pick_type(d):
    return random.choice(TYPES_SHORT if d < 700 else TYPES_MED if d < 2800 else TYPES_LONG)


def cruise_level(leg, a, b, D):
    """Realistic cruise level: eastbound odd thousands, westbound even (semicircular rule), varied per flight."""
    top = min(leg["maxalt"], max(8000, round(D * 95 / 1000) * 1000))
    trk = bearing(a["lat"], a["lon"], b["lat"], b["lon"])
    east = trk < 180
    h = sum(ord(ch) for ch in leg["cs"]) + int(leg["t0"]) // 7
    top -= (h % 3) * 2000                                # spread flights over several levels
    top = max(5000, top)
    k = top // 1000
    if top >= 29000:                                     # RVSM: east odd, west even
        if (k % 2 == 1) != east:
            k -= 1
    else:                                                # below FL290: east odd thousands, west even (+500 VFR not used)
        if (k % 2 == 1) != east:
            k -= 1
    return float(max(4000, k * 1000))


def leg_plan(leg):
    """Times (s from off-block) and distances (NM) of each flight phase, like a real flight profile."""
    a, b = AP_INDEX.get(leg["dep"]), AP_INDEX.get(leg["arr"])
    D = dist_nm(a["lat"], a["lon"], b["lat"], b["lon"])
    v, alt_max, roc = leg["kt"], leg["maxalt"], leg["roc"]
    alt = cruise_level(leg, a, b, D)
    climb_t = alt / roc * 60
    climb_d = (160 + v) / 2 * climb_t / 3600
    desc_d = alt / 1000 * 3.2
    desc_t = desc_d / ((v + 140) / 2) * 3600
    if climb_d + desc_d > D * 0.9:
        k = D * 0.9 / (climb_d + desc_d)
        alt *= k; climb_t *= k; climb_d *= k; desc_d *= k; desc_t *= k
    cruise_d = D - climb_d - desc_d
    cruise_t = cruise_d / v * 3600
    T1 = leg["taxi_out"]; T2 = T1 + climb_t; T3 = T2 + cruise_t; T4 = T3 + desc_t
    T5 = T4 + leg["taxi_in"]; T6 = T5 + leg["turn"]
    return {"a": a, "b": b, "D": D, "alt": alt, "climb_d": climb_d, "climb_t": climb_t, "cruise_d": cruise_d,
            "desc_d": desc_d, "desc_t": desc_t, "T": (T1, T2, T3, T4, T5, T6)}


class AiFeed(Feed):
    """Worldwide AI flights between real airports, computed from the clock: every flight has an off-block time,
    so its position depends only on the current (simulation) time. Closing the page or the server changes nothing;
    on restart each flight is exactly where it would be. 'Pause AI' freezes the simulation clock until resumed."""
    name, interval = "ai", 1

    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.flights, self.offset, self.paused_at, self.seq, self.ready = [], 0.0, None, 0, False
        self.plans = {}

    # ----- clock
    def sim_now(self):
        return (self.paused_at if self.paused_at else time.time()) - self.offset

    def set_paused(self, paused):
        with self.lock:
            if paused and not self.paused_at:
                self.paused_at = time.time()
            elif not paused and self.paused_at:
                self.offset += time.time() - self.paused_at
                self.paused_at = None
            self.save()
        log("AI flights " + ("paused" if paused else "resumed"))

    # ----- persistence
    def save(self):
        try:
            tmp = AI_STATE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "offset": self.offset, "paused_at": self.paused_at, "seq": self.seq,
                           "saved": time.time(), "flights": self.flights}, f)
            os.replace(tmp, AI_STATE_PATH)
        except Exception as e:
            log(f"AI state not saved: {e}")

    def start(self):
        with self.lock:
            if os.path.exists(AI_STATE_PATH):
                try:
                    with open(AI_STATE_PATH, encoding="utf-8") as f:
                        st = json.load(f)
                    self.offset, self.paused_at, self.seq = st.get("offset", 0.0), st.get("paused_at"), st.get("seq", 0)
                    self.flights = [f for f in st.get("flights", []) if f["leg"]["dep"] in AP_INDEX and f["leg"]["arr"] in AP_INDEX]
                    log(f"AI: restored {len(self.flights)} flights" + (" (paused)" if self.paused_at else ", continuing with the real clock"))
                except Exception as e:
                    log(f"AI state unreadable ({e}); starting fresh")
            self.resize(int(CFG.get("ai_flights", 300)))
            self.ready = True
            self.save()
        threading.Thread(target=self._autosave, daemon=True).start()

    def _autosave(self):
        while True:
            time.sleep(20)
            with self.lock:
                self.save()

    # ----- flight creation
    def pick_dest(self, dep):
        home = AP_INDEX.get(CFG["home"]["icao"])
        if home and dep["code"] != home["code"] and random.random() < CFG.get("ai_home_share", 0.1):
            d = dist_nm(dep["lat"], dep["lon"], home["lat"], home["lon"])
            if 150 < d < 7500:
                return home
        lo, hi = random.choice([(150, 700), (300, 2800), (300, 2800), (2800, 7500)])
        for _ in range(120):
            b = random.choice(LARGE)
            if b["code"] != dep["code"] and lo <= dist_nm(dep["lat"], dep["lon"], b["lat"], b["lon"]) <= hi:
                return b
        return random.choice([x for x in LARGE if x["code"] != dep["code"]])

    def new_leg(self, dep, t0, ac_type=None):
        arr = self.pick_dest(dep)
        d = dist_nm(dep["lat"], dep["lon"], arr["lat"], arr["lon"])
        typ = ac_type if ac_type and d < {"S": 900, "M": 3200, "L": 9e9}[ac_type[4]] else None
        if not typ:
            t = pick_type(d)
            size = "S" if t in TYPES_SHORT else "M" if t in TYPES_MED else "L"
            typ = list(t[:4]) + [size]
        return {"dep": dep["code"], "arr": arr["code"], "t0": t0, "cs": random.choice(AIRLINES) + str(random.randint(10, 4999)),
                "type": typ[0], "kt": typ[1], "maxalt": typ[2], "roc": typ[3], "size": typ[4],
                "taxi_out": random.uniform(6, 16) * 60, "taxi_in": random.uniform(3, 9) * 60, "turn": random.uniform(40, 110) * 60,
                "sq": "".join(random.choice("01234567") for _ in range(4))}

    def new_flight(self, now):
        home = AP_INDEX.get(CFG["home"]["icao"])
        dep = home if home and random.random() < CFG.get("ai_home_share", 0.1) else random.choice(LARGE)
        leg = self.new_leg(dep, now)
        T6 = leg_plan(leg)["T"][5]
        leg["t0"] = now - random.uniform(0, T6)
        self.seq += 1
        return {"id": f"ai-{self.seq:05d}", "leg": leg}

    def resize(self, n):
        n = max(0, min(5000, n))
        now = self.sim_now()
        if not LARGE:
            return
        normal = [f for f in self.flights if not f.get("shadow")]
        shadows = [f for f in self.flights if f.get("shadow")]
        while len(normal) < n:
            normal.append(self.new_flight(now))
        self.flights = normal[:n] + shadows

    def set_count(self, n):
        with self.lock:
            CFG["ai_flights"] = n
            self.resize(n)
            self.save()
        save_config()

    # ----- state at a time
    def plan(self, leg):
        key = (leg["dep"], leg["arr"], leg["t0"])
        p = self.plans.get(key)
        if not p:
            if len(self.plans) > 20000:
                self.plans.clear()
            p = self.plans[key] = leg_plan(leg)
        return p

    def advance(self, f, now):
        c = f.get("ctl")
        if not c:
            return
        if now - c["t"] > 6 * 3600:
            c["t"] = now - 6 * 3600
        while c["t"] < now - 1e-6:
            dt = min(1.0, now - c["t"])
            r = step_ctl(f, c, dt)
            c["t"] += dt
            if r:
                return

    def state(self, f, now):
        if f.get("ctl"):
            self.advance(f, now)
            c = f.get("ctl")
            if c:
                mode = {"hdg": "Radar vectors", "direct": "Direct " + c.get("dct", {}).get("code", ""),
                        "ils": ("Established ILS " if (c.get("ils") or {}).get("cap") else "Cleared ILS ") + (c.get("ils") or {}).get("id", ""),
                        "ron": "Own navigation", "app_auto": "Vectors to ILS " + (c.get("ils") or {}).get("id", ""),
                        "takeoff": "Takeoff roll" if c.get("ground") else "Initial climb"}.get(c["mode"], "Under control")
                arr = AP_INDEX.get(f["leg"]["arr"])
                eta = time.time() + dist_nm(c["lat"], c["lon"], arr["lat"], arr["lon"]) / max(c["gs"], 60) * 3600 if arr and not f.get("shadow") or (arr and f.get("shadow") and f["leg"]["arr"] != f["leg"]["dep"]) else None
                return {"phase": "ATC: " + mode, "lat": c["lat"], "lon": c["lon"], "alt": c["alt"], "gs": c["gs"], "vs": c["vs"],
                        "trk": c["trk"], "ground": bool(c.get("ground")), "off_block": None, "eta": eta, "dist": None}
            if f.get("remove"):
                return None
        if f.get("gnd"):
            return gnd_state(f, now)
        if f.get("twr"):
            return self.tower_hold_state(f)
        return self.tower_hook(f, now, self._sched(f, now))

    def tower_hold_state(self, f):
        tw = tower_ctx()
        tr = f["twr"]
        if not tw:
            f.pop("twr", None); f["released"] = f["leg"]["t0"]
            return None
        end = next((e for e in tw["ends"] if e["id"] == tr["rwy"]), tw["dep"])
        if tr["state"] == "lined":
            lat, lon = end["lat"], end["lon"]
        elif tr.get("pos"):
            lat, lon = tr["pos"][0], tr["pos"][1]
            return {"phase": "Holding short runway " + end["id"], "lat": lat, "lon": lon, "alt": end["elev"], "gs": 0, "vs": 0,
                    "trk": tr["pos"][2], "ground": True, "off_block": None, "eta": None, "dist": None}
        else:
            lat, lon = gc_move(end["lat"], end["lon"], (end["crs"] + 180) % 360, 0.06)
            lat, lon = gc_move(lat, lon, (end["crs"] + 90) % 360, 0.05)
        return {"phase": ("Lined up runway " if tr["state"] == "lined" else "Holding short runway ") + end["id"],
                "lat": lat, "lon": lon, "alt": end["elev"], "gs": 0, "vs": 0, "trk": end["crs"], "ground": True,
                "off_block": None, "eta": None, "dist": None}

    def tower_hook(self, f, now, s):
        tw = tower_ctx()
        if not tw or not s:
            return s
        leg = f["leg"]
        if f.get("gnd_done") == leg["t0"] and s["phase"] == "Landed, taxiing in":
            return None                               # parked at the gate after Ground taxi-in
        if leg["dep"] == tw["code"] and s["phase"] == "Taxi out" and f.get("gnd_done") != leg["t0"] and f.get("released") != leg["t0"]:
            gx = ground_ctx()
            if gx and not f.get("shadow"):
                gnd_new_dep(f, now, gx)
                return gnd_state(f, now)
        if leg["dep"] == tw["code"] and s["phase"] == "Climbing" and s["e"] - s["T1"] < 180 and f.get("released") != leg["t0"]:
            f["twr"] = {"state": "holding", "rwy": tw["dep"]["id"], "since": now}
            comm(leg["cs"], f"Tower, {spoken(leg['cs'])}, holding short runway {tw['dep']['id']}, ready for departure", f["id"], pk("tower"))
            return self.tower_hold_state(f)
        if leg["arr"] == tw["code"] and not s["ground"] and s["phase"] in ("Descending", "Approach", "Cruise") and f.get("arr_conv") != leg["t0"]:
            dd = dist_nm(s["lat"], s["lon"], tw["lat"], tw["lon"])
            if mine("approach") and dd < 40 and not f.get("shadow"):
                # you are Approach: the arrival calls you and waits for vectors (holds near the airport if ignored)
                f["arr_conv"] = leg["t0"]
                f["ctl"] = {"t": now, "lat": s["lat"], "lon": s["lon"], "alt": s["alt"], "gs": s["gs"], "trk": s["trk"], "vs": s["vs"],
                            "mode": "ron", "hdg": s["trk"], "dir": None, "alt_t": float(max(tw["elev"] + 5000, min(s["alt"], 11000))),
                            "spd": None, "await_app": True}
                comm(leg["cs"], f"Approach, {spoken(leg['cs'])}, {say_alt(s['alt'])}, inbound {tw['code']}, request vectors ILS runway {tw['arr']['id']}", f["id"], "pilot")
                return self.state(f, now)
            if dd < 35:
                end = tw["arr"]
                f["arr_conv"] = leg["t0"]
                f["ctl"] = {"t": now, "lat": s["lat"], "lon": s["lon"], "alt": s["alt"], "gs": s["gs"], "trk": s["trk"], "vs": s["vs"],
                            "mode": "app_auto", "hdg": s["trk"], "dir": None, "alt_t": float(min(s["alt"], end["elev"] + 3000)), "spd": None,
                            "auto": True, "faf": faf_of(end), "ils": {**end, "ap": tw["code"], "cap": False}}
                comm(leg["cs"], f"Approach, {spoken(leg['cs'])}, {say_alt(s['alt'])}, expecting ILS runway {end['id']}", f["id"], pk("approach"))
                return self.state(f, now)
        return s

    def _sched(self, f, now):
        leg = f["leg"]
        p = self.plan(leg)
        while now - leg["t0"] >= p["T"][5]:          # turnaround done -> next leg from the arrival airport
            ac = [leg["type"], leg["kt"], leg["maxalt"], leg["roc"], leg["size"]]
            leg = f["leg"] = self.new_leg(p["b"], leg["t0"] + p["T"][5], ac)
            p = self.plan(leg)
        T1, T2, T3, T4, T5, T6 = p["T"]
        e = now - leg["t0"]
        a, b, D = p["a"], p["b"], p["D"]
        wall = lambda s: leg["t0"] + s + self.offset
        info = {"off_block": wall(0), "eta": wall(T4), "dist": round(D), "e": e, "T1": T1}
        if e < 0 or e >= T5:
            return None                                  # at the gate: not shown
        if e < T1:
            return dict(info, phase="Taxi out", lat=a["lat"], lon=a["lon"], alt=num(a["elev"]), gs=15, vs=0,
                        trk=bearing(a["lat"], a["lon"], b["lat"], b["lon"]), ground=True)
        if e >= T4:
            return dict(info, phase="Landed, taxiing in", lat=b["lat"], lon=b["lon"], alt=num(b["elev"]), gs=15, vs=0,
                        trk=bearing(a["lat"], a["lon"], b["lat"], b["lon"]), ground=True)
        v = leg["kt"]
        if e < T2:
            fr = (e - T1) / p["climb_t"]
            s = p["climb_t"] / 3600 * (160 * fr + (v - 160) * fr * fr / 2)
            gs = 160 + (v - 160) * fr
            alt = p["alt"] * (1 - (1 - fr) ** 1.6)
            vs = p["alt"] * 1.6 * (1 - fr) ** 0.6 / (p["climb_t"] / 60)
            phase = "Climbing"
        elif e < T3:
            s = p["climb_d"] + v * (e - T2) / 3600
            gs, alt, vs, phase = v, p["alt"], 0, "Cruise"
        else:
            fr = (e - T3) / p["desc_t"]
            s = p["climb_d"] + p["cruise_d"] + p["desc_t"] / 3600 * (v * fr + (140 - v) * fr * fr / 2)
            gs = v + (140 - v) * fr
            alt = p["alt"] * (1 - fr)
            vs = -p["alt"] / (p["desc_t"] / 60)
            phase = "Descending" if fr < 0.75 else "Approach"
        f_ = max(0.0, min(1.0, s / D)) if D > 0 else 1.0
        lat, lon = gc_point(a["lat"], a["lon"], b["lat"], b["lon"], f_)
        trk = bearing(lat, lon, b["lat"], b["lon"]) if f_ < 0.999 else bearing(a["lat"], a["lon"], b["lat"], b["lon"])
        return dict(info, phase=phase, lat=lat, lon=lon, alt=alt, gs=gs, vs=vs, trk=trk, ground=False)

    def fetch(self):
        if not self.ready:
            self.note = "Waiting for airport data..."
            return []
        with self.lock:
            now, out = self.sim_now(), []
            frozen = bool(self.paused_at)
            ctx_c = center_ctx()
            CRUISE_SNAP.clear()
            for f in self.flights:
                s = self.state(f, now)
                if not s:
                    continue
                leg = f["leg"]
                c = f.get("ctl")
                extra = {}
                if c:
                    extra["ctl"] = {"mode": c["mode"], "hdg": c.get("hdg"), "alt": c["alt_t"], "spd": c.get("spd"),
                                    "rwy": (c.get("ils") or {}).get("id"), "cap": bool((c.get("ils") or {}).get("cap")), "clr": bool(c.get("clr"))}
                if f.get("shadow"):
                    extra["shadow"] = f["shadow"]
                if f.get("twr"):
                    extra["twr"] = f["twr"]["state"]
                gd = f.get("gnd")
                if gd:
                    extra["gnd"] = {"st": gd["st"], "gate": gd["gate"], "rwy": gd["rwy"], "xr": gd["xr"].replace("|", " and "),
                                    "path": [[round(p[0], 6), round(p[1], 6)] for p in gd["path"][gd["i"]:gd["i"] + 150]]
                                    if gd["st"] in ("taxi", "held", "hs_rwy", "push", "roll") else []}
                if f.get("alt_off") and s["phase"] == "Cruise" and not c:
                    s = dict(s, alt=s["alt"] + f["alt_off"])
                if not s["ground"] and not c:
                    CRUISE_SNAP.append((f, s))
                if not s["ground"]:
                    ctr = center_hook(f, s, ctx_c)
                    if ctr:
                        extra["ctr"] = ctr
                arr = leg["arr"] if not (f.get("shadow") and leg["arr"] == leg["dep"]) else ""
                out.append(target(f["id"], "ai", leg["cs"], leg["type"], s["lat"], s["lon"], s["alt"], s["gs"], s["trk"],
                                  s["vs"], leg["sq"], leg["dep"], arr, s["ground"], time.time(),
                                  phase=s["phase"], off_block=s["off_block"], eta=s["eta"], dist=s["dist"], frozen=frozen, **extra))
            try:
                ai_atc_tick(now)
            except Exception as e:
                log(f"AI ATC error: {e}")
            if any(f.get("remove") for f in self.flights):
                self.flights = [f for f in self.flights if not f.get("remove")]
            self.note = (f"PAUSED since {time.strftime('%H:%M', time.localtime(self.paused_at))} - " if frozen else "") + \
                        f"{len(out)} airborne or taxiing of {len(self.flights)} flights (others at the gate)"
            return out


# ---------------------------------------------------------------- ATC control (Stage 2)

TELEPHONY = {"AAL": "American", "UAL": "United", "DAL": "Delta", "SWA": "Southwest", "JBU": "JetBlue", "ASA": "Alaska",
             "ACA": "Air Canada", "WJA": "WestJet", "AMX": "Aeromexico", "BAW": "Speedbird", "DLH": "Lufthansa",
             "AFR": "Airfrance", "KLM": "KLM", "IBE": "Iberia", "RYR": "Ryanair", "EZY": "Easy", "THY": "Turkish",
             "UAE": "Emirates", "QTR": "Qatari", "ETD": "Etihad", "SIA": "Singapore", "CPA": "Cathay", "JAL": "Japan Air",
             "ANA": "All Nippon", "KAL": "Korean Air", "CCA": "Air China", "CES": "China Eastern", "QFA": "Qantas",
             "ANZ": "New Zealand", "AIC": "Air India", "IGO": "IFLY", "ETH": "Ethiopian", "SAA": "Springbok",
             "LAN": "LAN", "AZU": "Azul", "GLO": "Gol", "AVA": "Avianca", "SAS": "Scandinavian", "FIN": "Finnair",
             "TAP": "Air Portugal", "FDX": "FedEx", "UPS": "UPS", "SKW": "SkyWest", "ENY": "Envoy"}
COMMS = collections.deque(maxlen=400)
COMMS_SEQ = [0]


def spoken(cs):
    m = re.match(r"^([A-Z]{3})(\d+[A-Z]*)$", cs or "")
    return f"{TELEPHONY.get(m.group(1), m.group(1))} {m.group(2)}" if m else (cs or "")


def say_alt(ft):
    ft = int(round(ft / 100.0) * 100)
    return f"flight level {ft // 100:03d}" if ft >= 18000 else f"{ft:,}"


POSITIONS = ("ground", "tower", "approach", "center")
AI_VOICE = threading.local()


def mine(pos):
    """True if the user works this position; otherwise an AI controller handles it."""
    return bool((CFG.get("positions") or {}).get(pos, True))


def pk(pos):
    """Message kind for a pilot calling a position: spoken/highlighted if it is yours, dim 'ai' line otherwise."""
    return "pilot" if mine(pos) else "ai"


class ai_voice:
    """Inside this block every ATC/pilot line is marked as an AI-controller exchange (dim, not spoken)."""
    def __enter__(self):
        AI_VOICE.on = True

    def __exit__(self, *a):
        AI_VOICE.on = False


def comm(who, text, id_=None, kind="atc"):
    if getattr(AI_VOICE, "on", False) and kind in ("atc", "pilot", "unable"):
        kind = "ai"
    COMMS_SEQ[0] += 1
    COMMS.append({"seq": COMMS_SEQ[0], "t": time.time(), "who": who, "text": text, "id": id_, "kind": kind})


def perf(leg):
    prop = leg["type"] in ("AT76", "DH8D")
    return {"vmin": 115 if prop else 135, "vmax": leg["kt"] + 20, "roc": leg["roc"], "rod": 1500 if prop else 2200,
            "maxalt": leg["maxalt"], "cruise": leg["kt"], "app": 120 if prop else 140}


def find_runway(code, near_lat, near_lon, prefer=None):
    """Runway end `code` (e.g. 27L) at the preferred airport, else at the nearest airport within 60 NM that has it."""
    code = code.upper().lstrip("0") if code[:1] == "0" and len(code) > 2 else code.upper()
    cands = []
    for ident, ends in RWY_ENDS.items():
        for e in ends:
            if e["id"].upper().lstrip("0") == code.lstrip("0"):
                d = dist_nm(near_lat, near_lon, e["lat"], e["lon"])
                if d < 60 or ident == prefer:
                    cands.append((0 if ident == prefer else 1, d, ident, e))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[1]))
    _, _, ident, e = cands[0]
    a = AP_INDEX.get(ident.upper())
    return dict(e, ap=a["code"] if a else ident)


def ils_geom(c, il):
    """Along-track distance before the threshold (NM) and cross-track (NM, + = right of the final course)."""
    k = math.cos(math.radians(il["lat"]))
    dx, dy = (c["lon"] - il["lon"]) * 60 * k, (c["lat"] - il["lat"]) * 60
    ux, uy = math.sin(math.radians(il["crs"])), math.cos(math.radians(il["crs"]))
    return -(dx * ux + dy * uy), dx * uy - dy * ux


def step_ctl(f, c, dt):
    """Advance one controlled flight by dt seconds (<= 1)."""
    leg = f["leg"]; P = perf(leg)
    arr = AP_INDEX.get(leg["arr"]) if leg.get("arr") else None
    tgt_trk, auto_spd = c["trk"], None
    mode = c["mode"]
    if mode == "takeoff":
        return step_takeoff(f, c, dt)
    if mode == "hdg":
        tgt_trk = c["hdg"]
        rj = c.get("ga_rejoin")
        if rj and c["alt"] > rj["elev"] + 2000:
            c.pop("ga_rejoin")
            c.update(mode="app_auto", faf=faf_of(rj), ils=rj)
            comm(leg["cs"], f"{spoken(leg['cs'])}, re-joining for ILS runway {rj['id']}", f["id"], pk("approach"))
    elif mode == "direct":
        tgt_trk = bearing(c["lat"], c["lon"], c["dct"]["lat"], c["dct"]["lon"])
        if dist_nm(c["lat"], c["lon"], c["dct"]["lat"], c["dct"]["lon"]) < 1.0:
            c.update(mode="hdg", hdg=c["trk"], dir=None)
            comm(leg["cs"], f"{spoken(leg['cs'])}, passing {c['dct']['code']}, heading {int(c['trk']):03d}", f["id"], "pilot")
    elif mode == "ils":
        il = c["ils"]
        along, xtk = ils_geom(c, il)
        diff = abs((c["trk"] - il["crs"] + 540) % 360 - 180)
        if not il.get("cap"):
            tgt_trk = c["hdg"] if c.get("hdg") is not None else c["trk"]
            if c.get("auto") and 1 < along < 30:
                tgt_trk = (il["crs"] - 30) % 360 if xtk > 0 else (il["crs"] + 30) % 360
            if c.get("auto"):
                auto_spd = 210
            if abs(xtk) < 1.0 and 0.5 < along < 35 and diff < 90:
                il["cap"] = True
                c["dir"] = None
                comm(leg["cs"], f"{spoken(leg['cs'])}, established ILS runway {il['id']}", f["id"], pk("approach"))
        if il.get("cap"):
            tgt_trk = (il["crs"] - max(-30, min(30, xtk * 30))) % 360
            gs_alt = il["elev"] + along * 318
            if c["alt"] > gs_alt:
                c["alt_t"] = max(il["elev"], gs_alt)
            auto_spd = P["app"] if along < 5 else 170 if along < 12 else None
            tw = tower_ctx()
            if tw and il.get("ap") == tw["code"]:
                if along < 6 and not c.get("clr") and not c.get("call6"):
                    c["call6"] = True
                    comm(leg["cs"], f"Tower, {spoken(leg['cs'])}, 6 miles final runway {il['id']}", f["id"], pk("tower"))
                if along < 0.8:
                    occ = rwy_occupied(tw["code"], il, leg["cs"], c["t"])
                    if occ:
                        return go_around(f, c, il, f"runway occupied by {occ}")
                    if not c.get("clr"):
                        return go_around(f, c, il, "no landing clearance")
            if along < 0.2:
                return land(f, c, il["ap"], f"runway {il['id']}")
            if along < -2:   # missed the runway
                c.update(mode="hdg", hdg=il["crs"], alt_t=il["elev"] + 3000, ils=None)
                comm(leg["cs"], f"{spoken(leg['cs'])}, going around, runway heading, climbing {say_alt(il['elev'] + 3000)}", f["id"], "pilot")
    elif mode == "app_auto":
        il = c["ils"]; fa = c["faf"]
        tgt_trk = bearing(c["lat"], c["lon"], fa["lat"], fa["lon"])
        along, xtk = ils_geom(c, il)
        c["alt_t"] = min(c["alt_t"], il["elev"] + 3000)
        auto_spd = 210
        gap = app_gap(f, c, il)                       # AI approach: keep arrivals 5 NM apart
        if gap is not None and not mine("approach"):
            if gap < 3.0 and c["t"] - c.get("orbited_t", -1e9) > 240 and dist_nm(c["lat"], c["lon"], il["lat"], il["lon"]) > 12:
                c.update(mode="orbit", orbit_left=360, orbit_then="app_auto", dir="R")
                with ai_voice():
                    comm("APP", f"{spoken(leg['cs'])}, make a right 360 for spacing", f["id"])
                    comm(leg["cs"], f"right 360, {spoken(leg['cs'])}", f["id"], "pilot")
                return None
            if gap < 6.0:
                auto_spd = 170
        if dist_nm(c["lat"], c["lon"], fa["lat"], fa["lon"]) < 2.0 or (abs(xtk) < 1 and 3 < along < 11 and abs((c["trk"] - il["crs"] + 540) % 360 - 180) < 60):
            ih = (il["crs"] - 30) % 360 if xtk > 0.2 else (il["crs"] + 30) % 360 if xtk < -0.2 else il["crs"]
            c.update(mode="ils", hdg=ih, dir=None)
    elif mode == "orbit":
        tgt_trk = (c["trk"] + 90) % 360
        c["dir"] = "R"
        if c.get("orbit_left") is not None:
            c["orbit_left"] -= 3 * dt
            if c["orbit_left"] <= 0:
                c.update(mode=c.get("orbit_then") or "hdg", dir=None, hdg=c["trk"])
                c["orbited_t"] = c["t"]
    elif mode == "ron":
        tw = tower_ctx()
        if arr and tw and arr["code"] == tw["code"] and c.get("await_app") and mine("approach"):
            if dist_nm(c["lat"], c["lon"], arr["lat"], arr["lon"]) < 18:
                c.update(mode="orbit", orbit_left=None, dir="R")
                comm(leg["cs"], f"{spoken(leg['cs'])}, entering hold, {say_alt(c['alt'])}, request vectors", f["id"], "pilot")
                return None
        elif arr and tw and arr["code"] == tw["code"] and dist_nm(c["lat"], c["lon"], arr["lat"], arr["lon"]) < 35:
            end = tw["arr"]
            c.update(mode="app_auto", auto=True, faf=faf_of(end), ils={**end, "ap": tw["code"], "cap": False}, dir=None)
            comm(leg["cs"], f"Approach, {spoken(leg['cs'])}, {say_alt(c['alt'])}, expecting ILS runway {end['id']}", f["id"], pk("approach"))
            return None
        if arr and leg.get("dep") in AP_INDEX:
            dep = AP_INDEX[leg["dep"]]
            cruise = min(P["maxalt"], max(8000, round(dist_nm(dep["lat"], dep["lon"], arr["lat"], arr["lon"]) * 95 / 1000) * 1000))
            d0 = dist_nm(c["lat"], c["lon"], arr["lat"], arr["lon"])
            if d0 > (cruise - num(arr["elev"])) / 1000 * 3.2 + 30 and c["alt_t"] < cruise:
                c["alt_t"] = float(cruise)
    if mode == "ron":
        if arr:
            d = dist_nm(c["lat"], c["lon"], arr["lat"], arr["lon"])
            tgt_trk = bearing(c["lat"], c["lon"], arr["lat"], arr["lon"])
            elev = num(arr["elev"])
            if d < (c["alt"] - elev) / 1000 * 3.2 + 8:
                c["alt_t"] = max(elev, min(c["alt_t"], elev + max(0.0, d - 1) * 318))
            auto_spd = P["app"] if d < 6 else 180 if d < 15 else None
            if d < 1.3 and c["alt"] < elev + 900:
                return land(f, c, arr["code"], "")
    # turn (standard rate 3 deg/s)
    diff = (tgt_trk - c["trk"] + 540) % 360 - 180
    if c.get("dir") == "L" and diff > 1:
        diff -= 360
    elif c.get("dir") == "R" and diff < -1:
        diff += 360
    if abs(diff) < 1:
        c["dir"] = None
    c["trk"] = (c["trk"] + max(-3 * dt, min(3 * dt, diff))) % 360
    # climb / descend
    dz = c["alt_t"] - c["alt"]
    rate = (P["roc"] * max(0.35, 1 - c["alt"] / (P["maxalt"] * 1.15)) if dz > 0 else P["rod"]) / 60
    step = max(-rate * dt, min(rate * dt, dz))
    c["alt"] += step
    c["vs"] = step / dt * 60 if dt > 0 else 0
    # speed
    want = c["spd"] if c.get("spd") else (auto_spd or (min(250, P["cruise"]) if c["alt"] < 10000 else P["cruise"]))
    if c.get("spd") and auto_spd and auto_spd < c["spd"] and mode == "ils":
        want = auto_spd
    c["gs"] += max(-1.5 * dt, min(1.5 * dt, want - c["gs"]))
    c["lat"], c["lon"] = gc_move(c["lat"], c["lon"], c["trk"], c["gs"] * dt / 3600)
    return None


def gc_move(lat, lon, trk, d):
    p1, l1, b, a = math.radians(lat), math.radians(lon), math.radians(trk), d / 3440.065
    p2 = math.asin(math.sin(p1) * math.cos(a) + math.cos(p1) * math.sin(a) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(a) * math.cos(p1), math.cos(a) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def land(f, c, ap_code, rwy_txt):
    leg = f["leg"]
    gx = ground_ctx() if c.get("ils") and not f.get("shadow") else None
    if gx and ap_code != gx["tw"]["code"]:
        gx = None
    if c.get("ils"):
        occupy(ap_code, c["ils"], leg["cs"], None if gx else c["t"] + 45)
    comm(leg["cs"], f"{spoken(leg['cs'])} landed {ap_code} {rwy_txt}".strip(), f["id"], "event")
    f.pop("ctl", None)
    if gx:
        gnd_new_arr(f, c, c["t"], gx, c["ils"])
    if f.get("shadow"):
        f["remove"] = True
        return "landed"
    if ap_code in AP_INDEX and ap_code != leg["arr"]:
        leg["arr"] = ap_code
    p = leg_plan(leg) if leg["dep"] in AP_INDEX and leg["arr"] in AP_INDEX and leg["dep"] != leg["arr"] else None
    leg["t0"] = c["t"] - (p["T"][3] if p else 0)
    if f.get("gnd"):
        f["gnd"]["leg"] = leg["t0"]
    return "landed"


# ---------------------------------------------------------------- AI controllers (positions the user does not work)
CRUISE_SNAP = []        # (flight, state) of uncontrolled airborne AI flights, refreshed every traffic update
AI_ATC = {"last_dep": {}, "last_deconf": 0.0}


def ai_atc_tick(now):
    tw = tower_ctx()
    # approach sequence snapshot (used for AI spacing)
    APP_SEQ.clear()
    finals = []
    for f in AI.flights:
        c = f.get("ctl")
        if c and c["mode"] in ("ils", "app_auto", "orbit") and c.get("ils"):
            il = c["ils"]
            d = dist_nm(c["lat"], c["lon"], il["lat"], il["lon"])
            if c["mode"] != "orbit":
                APP_SEQ.setdefault(phys(il), []).append((d, f["id"]))
            if tw and il.get("ap") == tw["code"] and c["mode"] == "ils":
                finals.append((ils_geom(c, il)[0], f, c))
    if tw and not mine("tower"):
        with ai_voice():
            for along, f, c in finals:
                il, cs = c["ils"], f["leg"]["cs"]
                if il.get("cap") and along < 7 and not c.get("clr") and not rwy_occupied(tw["code"], il, cs, now):
                    c["clr"] = True
                    comm("TWR", f"{spoken(cs)}, runway {il['id']}, cleared to land", f["id"])
                    comm(cs, f"cleared to land runway {il['id']}, {spoken(cs)}", f["id"], "pilot")
            for f in AI.flights:
                tr = f.get("twr")
                if not tr:
                    continue
                end = next((e for e in tw["ends"] if e["id"] == tr["rwy"]), tw["dep"])
                key = phys(end)
                arr_close = any(along < 5 and phys(c["ils"]) == key for along, g, c in finals)
                occ = rwy_occupied(tw["code"], end, f["leg"]["cs"], now)
                if tr["state"] == "holding" and not occ and not arr_close and now - AI_ATC["last_dep"].get(key, 0) > 75:
                    tower_cmd(f, ["LUAW"], now)
                    tr["lined_t"] = now
                    break
                if tr["state"] == "lined" and now - tr.get("lined_t", now) > 10 and not arr_close:
                    ok, _ = tower_cmd(f, ["CTO"], now)
                    if ok:
                        AI_ATC["last_dep"][key] = now
                    break
    if now - AI_ATC["last_deconf"] >= 5:
        dt = now - AI_ATC["last_deconf"] if AI_ATC["last_deconf"] else 5
        AI_ATC["last_deconf"] = now
        deconflict(now, min(dt, 30))


def deconflict(now, dt):
    """AI enroute control: uncontrolled AI flights predicted within 5 NM / 1,000 ft get a 1,000-2,000 ft level change."""
    cells = {}
    for f, s in CRUISE_SNAP:
        if s["phase"] != "Cruise":
            continue
        cells.setdefault((int(s["lat"] * 2), int(s["lon"] * 2)), []).append((f, s))
    for f, s in CRUISE_SNAP:                           # ease towards the target offset (1,500 ft/min)
        tgt = f.get("alt_off_t", 0)
        if s["phase"] != "Cruise":
            f.pop("alt_off", None); f.pop("alt_off_t", None)
            continue
        cur = f.get("alt_off", 0)
        if cur != tgt:
            stp = 25 * dt
            f["alt_off"] = tgt if abs(tgt - cur) <= stp else cur + stp * (1 if tgt > cur else -1)
    for (ci, cj), lst in cells.items():
        near = []
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                near += cells.get((ci + di, cj + dj), [])
        for f, s in lst:
            for g, t in near:
                if g["id"] <= f["id"]:
                    continue
                if abs(s["alt"] - t["alt"]) >= 1000:
                    continue
                hit = False
                for sec in (0, 60, 120):
                    a = gc_move(s["lat"], s["lon"], s["trk"], s["gs"] * sec / 3600)
                    b = gc_move(t["lat"], t["lon"], t["trk"], t["gs"] * sec / 3600)
                    if dist_nm(a[0], a[1], b[0], b[1]) < 5:
                        hit = True
                        break
                if hit:
                    mover = g if not g.get("alt_off_t") else f
                    st = t if mover is g else s
                    base = st["alt"] - mover.get("alt_off", 0)
                    off = 2000 if base + 2000 <= mover["leg"]["maxalt"] else -2000
                    mover["alt_off_t"] = mover.get("alt_off_t", 0) + off if abs(mover.get("alt_off_t", 0) + off) <= 4000 else -off


def set_positions(body):
    ps = CFG.setdefault("positions", {})
    for k in POSITIONS:
        if k in body:
            ps[k] = bool(body[k])
    save_config()
    log(f"Positions: yours = {[k for k in POSITIONS if ps.get(k)]}, AI = {[k for k in POSITIONS if not ps.get(k)]}")
    return {"positions": ps}, 200


# ---------------------------------------------------------------- Center (Stage 2, round 3)
# The Center sector is a circle around the home airport (default 200 NM). AI flights inside it check in with Center
# (arrivals to the home airport, departures from it, overflights). You control them like any AI flight and hand them off:
# arrivals to Approach (they then fly the automatic ILS approach), departures and overflights to the next sector.

def center_ctx():
    cc = CFG.get("center") or {}
    if not cc.get("enabled") or not AP_STATE["ready"]:
        return None
    a = AP_INDEX.get((CFG["home"].get("icao") or "").upper())
    if not a:
        return None
    return {"code": a["code"], "lat": a["lat"], "lon": a["lon"], "r": float(cc.get("radius", 200)),
            "app_r": 40.0, "elev": num(a["elev"])}


def center_cat(f, cx):
    leg = f["leg"]
    return "arr" if leg["arr"] == cx["code"] else "dep" if leg["dep"] == cx["code"] else "ovf"


def center_hook(f, s, cx):
    """Called for every airborne AI flight each second: check-in when entering the sector, note when leaving.
    Returns the flight's Center state for the page ('in', 'handed', or None)."""
    if not cx or (f.get("shadow") and not f.get("ctl")) or not mine("center"):
        return None
    leg = f["leg"]
    if abs(s["lat"] - cx["lat"]) > cx["r"] / 60 + 1 and not (f.get("ctr") and f["ctr"].get("st") == "in"):
        return None                                           # quick reject: far away
    d = dist_nm(s["lat"], s["lon"], cx["lat"], cx["lon"])
    st = f.get("ctr")
    if st and st.get("leg") != leg["t0"]:
        st = f["ctr"] = None
    cat = center_cat(f, cx)
    inside = d <= cx["r"]
    if not st:
        if not inside:
            return None
        # arrivals already inside approach airspace, and departures still low and close, belong to Approach/Tower
        if cat == "arr" and d < cx["app_r"]:
            return None
        if cat == "dep" and (d < 25 or s["alt"] < 9000):
            return None
        f["ctr"] = st = {"leg": leg["t0"], "st": "in", "cat": cat}
        cs = leg["cs"]
        if cat == "dep":
            comm(cs, f"Center, {spoken(cs)}, passing {say_alt(s['alt'])}, climbing", f["id"], "pilot")
        elif cat == "arr":
            comm(cs, f"Center, {spoken(cs)}, {say_alt(s['alt'])}, inbound {cx['code']}", f["id"], "pilot")
        else:
            comm(cs, f"Center, {spoken(cs)}, {say_alt(s['alt'])}", f["id"], "pilot")
        return "in"
    if st["st"] == "in" and not inside:
        st["st"] = "out"
        comm("NIGHTLOOP", f"{leg['cs']} left your sector without a handoff", f["id"], "unable")
    if st["st"] == "in" and cat == "arr" and d < cx["app_r"]:
        st["st"] = "handed"      # flew into approach airspace on its own: Approach takes it
        comm("NIGHTLOOP", f"{leg['cs']} entered approach airspace without a handoff", f["id"], "unable")
    return st["st"] if inside and st["st"] in ("in", "handed") else None


def center_handoff(f, c):
    """HO command: arrivals go to Approach (automatic ILS once within 35 NM), others to the next sector (own navigation)."""
    leg = f["leg"]
    cx = center_ctx()
    st = f.get("ctr") or {}
    f["ctr"] = dict(st, leg=leg["t0"], st="handed", cat=st.get("cat") or (center_cat(f, cx) if cx else "ovf"))
    if c["mode"] not in ("ils", "app_auto", "takeoff"):
        c.update(mode="ron", dir=None)
        c.pop("dct", None)
    if cx and leg["arr"] == cx["code"]:
        return f"contact {cx['code']} Approach, good day", "over to approach, good day"
    return "contact next Center, good day", "next Center, good day"


def api_center(q):
    cc = CFG.get("center") or {}
    out = {"enabled": bool(cc.get("enabled")), "radius": float(cc.get("radius", 200)), "arr": [], "dep": [], "ovf": []}
    cx = center_ctx()
    if not cx:
        return out
    with AI.lock:
        now = AI.sim_now()
        for f in AI.flights:
            st = f.get("ctr")
            if not st or st.get("leg") != f["leg"]["t0"] or st["st"] not in ("in", "handed"):
                continue
            s = AI.state(f, now)
            if not s or s["ground"]:
                continue
            leg = f["leg"]
            d = dist_nm(s["lat"], s["lon"], cx["lat"], cx["lon"])
            if d > cx["r"] + 5 or (st["st"] == "handed" and st["cat"] == "arr" and d < cx["app_r"]):
                continue
            c = f.get("ctl")
            out[st["cat"]].append({"id": f["id"], "cs": leg["cs"], "type": leg["type"], "alt": round(s["alt"]), "gs": round(s["gs"]),
                                   "dist": round(d), "dep": leg["dep"], "arr": leg["arr"], "st": st["st"],
                                   "ctl": c["mode"] if c else None, "vs": round(s["vs"])})
    for k in ("arr", "dep", "ovf"):
        out[k].sort(key=lambda x: x["dist"])
    return out


def set_center(body):
    cc = CFG.setdefault("center", {"enabled": True, "radius": 200})
    if "enabled" in body:
        cc["enabled"] = bool(body["enabled"])
    if "radius" in body:
        cc["radius"] = max(60.0, min(400.0, num(body["radius"], 200)))
    save_config()
    log(f"Center: {cc}")
    return api_center({}), 200


# ---------------------------------------------------------------- Tower (Stage 2, round 2)

RWY_OCC = {}   # (airport code, physical runway) -> {"cs": callsign, "until": sim time or None (= until released)}


def tower_ctx():
    tc = CFG.get("tower") or {}
    if not tc.get("enabled") or not AP_STATE["ready"]:
        return None
    a = AP_INDEX.get((CFG["home"].get("icao") or "").upper())
    ends = RWY_ENDS.get(a["ident"]) if a else None
    if not ends:
        return None
    longest = max(ends, key=lambda x: x.get("len", 0))
    pick = lambda want: next((x for x in ends if x["id"] == want), longest)
    return {"code": a["code"], "ident": a["ident"], "lat": a["lat"], "lon": a["lon"], "elev": num(a["elev"]),
            "ends": ends, "arr": pick(tc.get("arr_rwy")), "dep": pick(tc.get("dep_rwy"))}


def phys(end):
    return "/".join(sorted([end["id"], end.get("pair") or ""])).strip("/")


def occupy(ap, end, cs, until):
    RWY_OCC[(ap, phys(end))] = {"cs": cs, "until": until}


def release_rwy(cs):
    for k in [k for k, v in RWY_OCC.items() if v["cs"] == cs]:
        RWY_OCC.pop(k, None)


def rwy_occupied(ap, end, me, now):
    v = RWY_OCC.get((ap, phys(end)))
    if v and v["cs"] != me and (v["until"] is None or v["until"] > now):
        return v["cs"]
    return None


def faf_of(end, nm=10):
    return dict(zip(("lat", "lon"), gc_move(end["lat"], end["lon"], (end["crs"] + 180) % 360, nm)))


APP_SEQ = {}   # physical runway -> [(distance to threshold, flight id)] from the last traffic update


def app_gap(f, c, il):
    """Distance (NM) to the nearest arrival ahead on the same runway, or None."""
    lst = APP_SEQ.get(phys(il))
    if not lst:
        return None
    me = dist_nm(c["lat"], c["lon"], il["lat"], il["lon"])
    ahead = [me - d for d, i in lst if i != f["id"] and d < me]
    return min(ahead) if ahead else None


def go_around(f, c, il, reason):
    leg = f["leg"]
    comm(leg["cs"], f"{spoken(leg['cs'])}, going around, {reason}", f["id"], "unable" if mine("tower") or mine("approach") else "ai")
    if c.get("auto") and not mine("approach"):
        c["ga_rejoin"] = dict(il, cap=False)
    c.update(mode="hdg", hdg=il["crs"], dir=None, alt_t=il["elev"] + 3000, clr=False, call6=False, ils=None)
    return None


def step_takeoff(f, c, dt):
    leg = f["leg"]; P = perf(leg)
    vr = P["vmin"] + 10
    if c.get("ground"):
        c["gs"] = min(vr, c["gs"] + 4 * dt)
        c["vs"] = 0
        c["lat"], c["lon"] = gc_move(c["lat"], c["lon"], c["trk"], c["gs"] * dt / 3600)
        if c["gs"] >= vr:
            c["ground"] = False
        return None
    c["alt"] += P["roc"] / 60 * dt
    c["vs"] = P["roc"]
    c["gs"] = min(250 if c["alt"] < 10000 else P["cruise"], c["gs"] + 1.5 * dt)
    c["lat"], c["lon"] = gc_move(c["lat"], c["lon"], c["trk"], c["gs"] * dt / 3600)
    if c["alt"] >= c["elev0"] + (1500 if c.get("after") == "ron" else 400):
        release_rwy(leg["cs"])
        comm(leg["cs"], f"{spoken(leg['cs'])}, airborne, passing {say_alt(c['alt'])}, climbing {say_alt(c['alt_t'])}", f["id"], pk("tower"))
        if c.get("after") == "ron":
            c.update(mode="ron", dir=None)
        else:
            c.update(mode="hdg", hdg=c.get("hdg_after", c["trk"]), dir=None)
    return None


def tower_cmd(f, toks, now):
    """Instructions for a flight holding short / lined up at the tower airport."""
    tw = tower_ctx()
    leg = f["leg"]; tr = f["twr"]; cs = leg["cs"]; P = perf(leg)
    if not tw:
        f.pop("twr", None); f["released"] = leg["t0"]
        return False, "Tower is switched off - the flight continues on its own"
    end = next((e for e in tw["ends"] if e["id"] == tr["rwy"]), tw["dep"])
    hdg, alt, dirn, act, ron = None, None, None, None, False
    bad = []
    for t in toks:
        m = re.match(r"^(HDG|H|TL|TR|L|R)(\d{1,3})$", t)
        if m:
            hdg = int(m.group(2)) % 360; dirn = {"L": "L", "TL": "L", "R": "R", "TR": "R"}.get(m.group(1)); continue
        m = re.match(r"^(FL|A|C)(\d{2,5})$", t)
        if m:
            v = int(m.group(2)); alt = v * 100 if (len(m.group(2)) <= 3 or m.group(1) == "FL") else v; continue
        if t in ("CTO", "TAKEOFF", "CT"):
            act = "cto"; continue
        if t in ("LUAW", "LU", "LINEUP"):
            act = act or "luaw"; continue
        if t in ("RON", "OWN"):
            ron = True; continue
        bad.append(t)
    if not act:
        return False, f"{cs} is waiting at runway {end['id']}: use LUAW (line up and wait) or CTO (cleared for takeoff)" + (f". Not understood: {' '.join(bad)}" if bad else "")
    occ = rwy_occupied(tw["code"], end, cs, now)
    if act == "luaw":
        tr["state"] = "lined"
        occupy(tw["code"], end, cs, None)
        comm("TWR", f"{spoken(cs)}, runway {end['id']}, line up and wait", f["id"], "atc")
        comm(cs, f"lining up runway {end['id']}, {spoken(cs)}", f["id"], "pilot")
        if occ:
            comm("NIGHTLOOP", f"Warning: runway {end['id']} is occupied by {occ}", f["id"], "unable")
        return True, f"line up and wait runway {end['id']}"
    if occ:
        comm(cs, f"{spoken(cs)}, holding position, runway {end['id']} is occupied by {occ}", f["id"], "unable")
        return False, f"Runway {end['id']} occupied by {occ}"
    if alt and alt > P["maxalt"]:
        alt = P["maxalt"]
    after = "hdg" if (hdg is not None or (alt and not ron)) else "ron"
    a = AP_INDEX.get(leg["arr"])
    D = dist_nm(end["lat"], end["lon"], a["lat"], a["lon"]) if a else 300
    cruise = min(P["maxalt"], max(8000, round(D * 95 / 1000) * 1000))
    f.pop("twr", None)
    f["released"] = leg["t0"]
    f["ctl"] = {"t": now, "lat": end["lat"], "lon": end["lon"], "alt": end["elev"], "gs": 0.0, "trk": end["crs"], "vs": 0,
                "mode": "takeoff", "ground": True, "elev0": end["elev"], "after": after, "dir": dirn,
                "hdg": end["crs"], "hdg_after": float(hdg) if hdg is not None else end["crs"],
                "alt_t": float(alt or (cruise if after == "ron" else end["elev"] + 4000 - (end["elev"] + 4000) % 1000)), "spd": None}
    occupy(tw["code"], end, cs, None)
    extra = []
    if hdg is not None:
        extra.append(f"fly heading {hdg or 360:03d}")
    if alt:
        extra.append(f"climb and maintain {say_alt(alt)}")
    if after == "ron" and not alt:
        extra.append("own navigation")
    txt = f"runway {end['id']}, cleared for takeoff" + (", " + ", ".join(extra) if extra else "")
    comm("TWR", f"{spoken(cs)}, {txt}", f["id"], "atc")
    comm(cs, f"cleared for takeoff runway {end['id']}" + (", " + ", ".join(extra) if extra else "") + f", {spoken(cs)}", f["id"], "pilot")
    return True, txt


def api_tower(q):
    tc = CFG.get("tower") or {}
    out = {"enabled": bool(tc.get("enabled")), "airport": CFG["home"].get("icao"), "runways": [], "arr_rwy": None,
           "dep_rwy": None, "holding": [], "finals": [], "occupied": []}
    a = AP_INDEX.get((CFG["home"].get("icao") or "").upper()) if AP_STATE["ready"] else None
    if a:
        out["runways"] = [{"id": e["id"], "crs": round(e["crs"]), "len": int(e.get("len", 0)), "lat": e["lat"], "lon": e["lon"], "elev": e["elev"]}
                          for e in RWY_ENDS.get(a["ident"], [])]
        out["home"] = {"icao": a["code"], "name": a["name"], "lat": a["lat"], "lon": a["lon"]}
    tw = tower_ctx()
    if not tw:
        return out
    out.update(arr_rwy=tw["arr"]["id"], dep_rwy=tw["dep"]["id"])
    with AI.lock:
        now = AI.sim_now()
        for f in AI.flights:
            leg, c = f["leg"], f.get("ctl")
            if f.get("twr"):
                out["holding"].append({"id": f["id"], "cs": leg["cs"], "rwy": f["twr"]["rwy"], "state": f["twr"]["state"],
                                       "waited": int(now - f["twr"]["since"]), "arr": leg["arr"], "type": leg["type"]})
            elif c and c["mode"] == "takeoff":
                out["holding"].append({"id": f["id"], "cs": leg["cs"], "rwy": "", "state": "takeoff roll", "waited": 0,
                                       "arr": leg["arr"], "type": leg["type"]})
            if c and c["mode"] in ("ils", "app_auto") and (c.get("ils") or {}).get("ap") == tw["code"]:
                along, _ = ils_geom(c, c["ils"])
                if not c["ils"].get("cap"):
                    along = dist_nm(c["lat"], c["lon"], c["ils"]["lat"], c["ils"]["lon"])
                out["finals"].append({"id": f["id"], "cs": leg["cs"], "rwy": c["ils"]["id"], "dist": round(along, 1),
                                      "clr": bool(c.get("clr")), "est": bool(c["ils"].get("cap")), "type": leg["type"]})
    out["finals"].sort(key=lambda x: x["dist"])
    for (ap, k), v in RWY_OCC.items():
        if ap == tw["code"] and (v["until"] is None or v["until"] > now):
            out["occupied"].append({"rwy": k, "cs": v["cs"]})
    return out


def set_tower(body):
    tc = CFG.setdefault("tower", {"enabled": True, "arr_rwy": "", "dep_rwy": ""})
    for k in ("enabled", "arr_rwy", "dep_rwy"):
        if k in body:
            tc[k] = bool(body[k]) if k == "enabled" else str(body[k]).upper()
    save_config()
    if not tc["enabled"]:
        with AI.lock:
            for f in AI.flights:
                if f.get("twr"):
                    f.pop("twr"); f["released"] = f["leg"]["t0"]
    log(f"Tower: {tc}")
    return api_tower({}), 200


def parse_and_apply(text, sel_id):
    """Typed command, e.g. 'DAL1776 H270 A050 S210', 'L 090', 'D KORD', 'ILS 27L', 'RON'. Returns (ok, message)."""
    raw = (text or "").upper().replace(",", " ").split()
    if not raw:
        return False, "Empty command"
    with AI.lock:
        byc = {fl["leg"]["cs"]: fl for fl in AI.flights}
        f = None
        if raw[0] in byc:
            f = byc[raw.pop(0)]
        elif sel_id:
            f = next((fl for fl in AI.flights if fl["id"] == sel_id), None)
        if not f:
            return False, "No AI flight selected. Select an AI plane or start the command with its callsign. VATSIM, IVAO and real flights can't be controlled - use Shadow."
        toks, i = [], 0
        while i < len(raw):
            t = raw[i]
            if t in ("H", "HDG", "L", "R", "TL", "TR", "A", "FL", "C", "S", "SPD", "I", "ILS") and i + 1 < len(raw) and raw[i + 1][:1].isdigit():
                t += raw[i + 1]; i += 1
            elif t in ("D", "DCT", "DIR") and i + 1 < len(raw):
                t = "D:" + raw[i + 1]; i += 1
            toks.append(t); i += 1
        if not toks:
            return False, "No instruction given"
        now = AI.sim_now()
        if AI.paused_at:
            return False, "AI planes are paused - resume them first"
        if f.get("gnd"):
            AI.state(f, now)
            if f.get("gnd"):
                ok, msg = ground_cmd(f, raw, now)
                AI.save()
                return ok, msg
        if f.get("twr"):
            ok, msg = tower_cmd(f, toks, now)
            AI.save()
            return ok, msg
        if not f.get("ctl"):
            st = AI.state(f, now)
            if not st:
                return False, f"{f['leg']['cs']} is parked at the gate"
            if st["ground"]:
                return False, f"{f['leg']['cs']} is on the ground (Tower control comes in a later stage)"
            f["ctl"] = {"t": now, "lat": st["lat"], "lon": st["lon"], "alt": st["alt"], "gs": st["gs"], "trk": st["trk"],
                        "vs": st["vs"], "mode": "hdg", "hdg": st["trk"], "dir": None,
                        "alt_t": float(round(st["alt"] / 100) * 100), "spd": None}
        else:
            AI.advance(f, now)
            if not f.get("ctl"):
                return False, f"{f['leg']['cs']} has already landed"
        c, leg, P = f["ctl"], f["leg"], perf(f["leg"])
        said, read, unable = [], [], []
        for t in toks:
            m = re.match(r"^(HDG|H|TL|TR|L|R)(\d{1,3})$", t)
            if m:
                h = int(m.group(2)) % 360
                d = {"L": "L", "TL": "L", "R": "R", "TR": "R"}.get(m.group(1))
                if c["mode"] == "ils" and not c["ils"].get("cap"):
                    c.update(hdg=float(h), dir=d)          # vectors to intercept, ILS stays armed
                else:
                    c.update(mode="hdg", hdg=float(h), dir=d)
                    c.pop("dct", None)
                diffh = (h - c["trk"] + 540) % 360 - 180
                word = "turn left" if d == "L" or (not d and diffh < -5) else "turn right" if d == "R" or (not d and diffh > 5) else "fly"
                said.append(f"{word} heading {h or 360:03d}"); read.append(f"{word.replace('turn ', '')} heading {h or 360:03d}")
                continue
            m = re.match(r"^(FL|A|C)(\d{2,5})$", t)
            if m:
                v = int(m.group(2)); ft = v * 100 if (len(m.group(2)) <= 3 or m.group(1) == "FL") else v
                if ft > P["maxalt"]:
                    unable.append(f"unable {say_alt(ft)}, maximum {say_alt(P['maxalt'])}")
                    continue
                verb = "climb and maintain" if ft > c["alt"] + 50 else "descend and maintain" if ft < c["alt"] - 50 else "maintain"
                c["alt_t"] = float(ft)
                said.append(f"{verb} {say_alt(ft)}"); read.append(f"{verb.split(' and')[0]} {say_alt(ft)}")
                continue
            m = re.match(r"^(SPD|S)(\d{2,3})$", t)
            if m:
                v = int(m.group(2))
                if v < P["vmin"] or v > P["vmax"]:
                    unable.append(f"unable {v} knots")
                    continue
                c["spd"] = float(v)
                said.append(f"maintain {v} knots"); read.append(f"{v} knots")
                continue
            if t in ("SR", "RS", "RNS"):
                c["spd"] = None; said.append("resume normal speed"); read.append("normal speed")
                continue
            code = t[2:] if t.startswith("D:") else (t[1:] if re.match(r"^D[A-Z0-9]{3,4}$", t) and t[1:] in AP_INDEX else None)
            if code is not None:
                a = find_point(code, c["lat"], c["lon"])
                if not a:
                    unable.append(f"unable direct {code}, unknown point")
                    continue
                c.update(mode="direct", dct={"code": a["code"], "lat": a["lat"], "lon": a["lon"]}, dir=None)
                said.append(f"proceed direct {a['code']}"); read.append(f"direct {a['code']}")
                continue
            if t in ("HO", "CA", "CC", "HANDOFF", "CONTACT"):
                said_, read_ = center_handoff(f, c)
                said.append(said_); read.append(read_)
                continue
            m = re.match(r"^(ILS|I)(\d{1,2}[LRC]?)$", t)
            if m:
                rw = find_runway(m.group(2), c["lat"], c["lon"], prefer=(AP_INDEX.get(leg["arr"]) or {}).get("ident"))
                if not rw:
                    unable.append(f"unable ILS {m.group(2)}, no such runway within 60 NM")
                    continue
                c.update(mode="ils", ils={**rw, "cap": False}, hdg=c["hdg"] if c["mode"] == "hdg" and c.get("hdg") is not None else c["trk"])
                c.pop("dct", None)
                said.append(f"cleared ILS runway {rw['id']} approach ({rw['ap']})"); read.append(f"cleared ILS {rw['id']}")
                continue
            if t in ("CL", "CTL", "CLR", "CLAND"):
                il = c.get("ils") or {}
                tw = tower_ctx()
                if c["mode"] not in ("ils", "app_auto") or not il:
                    unable.append("unable, not on an approach")
                    continue
                c["clr"] = True
                said.append(f"runway {il['id']}, cleared to land"); read.append(f"cleared to land runway {il['id']}")
                occ = rwy_occupied(il.get("ap"), il, leg["cs"], now) if tw else None
                if occ:
                    comm("NIGHTLOOP", f"Warning: runway {il['id']} is occupied by {occ}", f["id"], "unable")
                continue
            if t in ("GA", "GOAROUND"):
                il = c.get("ils")
                if c["mode"] not in ("ils", "app_auto") or not il:
                    unable.append("unable, not on an approach")
                    continue
                said.append("go around"); read.append("going around")
                go_around(f, c, il, "as instructed")
                continue
            if t in ("CTO", "LUAW", "TAKEOFF"):
                unable.append("unable, already airborne")
                continue
            if t in ("RON", "RES", "RESUME", "OWN"):
                if not AP_INDEX.get(leg.get("arr") or "") or (f.get("shadow") and leg["arr"] == leg["dep"]):
                    unable.append("unable own navigation, no destination known")
                    continue
                c.update(mode="ron", dir=None)
                c.pop("dct", None)
                said.append("resume own navigation"); read.append(f"own navigation to {leg['arr']}")
                continue
            unable.append(f"say again '{t}'")
        if said:
            comm("ATC", f"{spoken(leg['cs'])}, " + ", ".join(said), f["id"], "atc")
        if read or unable:
            comm(leg["cs"], ", ".join(read + unable) + f", {spoken(leg['cs'])}", f["id"], "pilot" if not unable else "unable")
        AI.save()
        return bool(said), (", ".join(said) if said else "; ".join(unable))


def shadow(body):
    src = find_target(body.get("id", ""))
    if not src:
        return {"error": "aircraft not found"}, 404
    if src["source"] == "ai":
        return {"error": "AI flights can be controlled directly"}, 400
    if src["ground"]:
        return {"error": "Only airborne flights can be shadowed for now"}, 400
    if not LARGE:
        return {"error": "airport data still loading"}, 503
    with AI.lock:
        now = AI.sim_now()
        near_ap = min(LARGE, key=lambda a: dist_nm(src["lat"], src["lon"], a["lat"], a["lon"]))
        dep = src["dep"] if src["dep"] in AP_INDEX else near_ap["code"]
        arr = src["arr"] if src["arr"] in AP_INDEX else ""
        typ = next((t for t in TYPES_SHORT + TYPES_MED + TYPES_LONG if t[0] == src["type"]), TYPES_MED[1])
        leg = {"dep": dep, "arr": arr or dep, "t0": now, "cs": src["callsign"].strip()[:10] or "SHADOW",
               "type": src["type"] or typ[0], "kt": typ[1], "maxalt": typ[2], "roc": typ[3], "size": "M",
               "taxi_out": 600, "taxi_in": 300, "turn": 3600, "sq": src["squawk"] or "2000"}
        AI.seq += 1
        fid = f"ai-shadow-{AI.seq:05d}"
        alt = max(1000.0, float(src["alt"]))
        f = {"id": fid, "leg": leg, "shadow": src["source"],
             "ctl": {"t": now, "lat": src["lat"], "lon": src["lon"], "alt": alt, "gs": max(120.0, float(src["gs"])),
                     "trk": float(src["track"]), "vs": 0, "mode": "hdg", "hdg": float(src["track"]), "dir": None,
                     "alt_t": round(alt / 100) * 100, "spd": None}}
        AI.flights.append(f)
        AI.save()
    comm("ATC", f"Shadow copy of {src['callsign']} ({NAMES_SRC.get(src['source'], src['source'])}) created - it is now yours to control", fid, "event")
    return {"id": fid, "callsign": leg["cs"]}, 200


NAMES_SRC = {"vatsim": "VATSIM", "ivao": "IVAO", "real": "real world"}


def api_comms(q):
    since = int(num(q.get("since"), 0))
    return {"seq": COMMS_SEQ[0], "items": [m for m in COMMS if m["seq"] > since][-100:]}


def api_runways(q):
    """Runway ends for the selected flight: destination airport first, then airports within 40 NM."""
    t = find_target(q.get("id", ""))
    if not t:
        return {"runways": []}
    out, seen = [], set()
    arr = AP_INDEX.get(t["arr"]) if t.get("arr") else None
    idents = []
    if arr:
        idents.append(arr["ident"])
    for ident, ends in RWY_ENDS.items():
        if ends and dist_nm(t["lat"], t["lon"], ends[0]["lat"], ends[0]["lon"]) < 40 and ident not in idents:
            a = AP_INDEX.get(ident.upper())
            if a and a["type"] != "small_airport":
                idents.append(ident)
    for ident in idents[:6]:
        a = AP_INDEX.get(ident.upper())
        for e in RWY_ENDS.get(ident, []):
            k = (ident, e["id"])
            if k not in seen:
                seen.add(k); out.append({"ap": a["code"] if a else ident, "id": e["id"], "crs": round(e["crs"])})
    return {"runways": out}


def api_command(body):
    ok, msg = parse_and_apply(body.get("text", ""), body.get("id"))
    AI.wake.set()
    return {"ok": ok, "message": msg}, 200 if ok else 400


# ---------------------------------------------------------------- Ground (Stage 2, round 4)
# Taxiways, runways and gates come from OpenStreetMap (Overpass API), downloaded once per airport to data/ground_<ICAO>.json.
# If that fails, a simple layout is used (stands around the airport, straight taxi lines).
# Departures from the home airport appear at a gate and call Ground; after PB / TAXI they taxi to the departure runway,
# stop before any runway they must cross (CROSS), and are handed to Tower at the holding point.
# Arrivals roll out after landing, leave the runway and call Ground for taxi to a gate.

GND = {"icao": None, "state": "idle", "note": "", "g": None, "src": ""}
GND_LOCK = threading.Lock()
OVERPASS = "https://overpass-api.de/api/interpreter"
OVERPASS_MIRRORS = [OVERPASS, "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter",
                    "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
TAXI_KT, PUSH_KT = 18.0, 3.0


def m_dist(a_lat, a_lon, b_lat, b_lon):
    k = math.cos(math.radians((a_lat + b_lat) / 2))
    return math.hypot((b_lat - a_lat) * 111195.0, (b_lon - a_lon) * 111195.0 * k)


def ground_ctx():
    gc = CFG.get("ground") or {}
    tw = tower_ctx()
    if not gc.get("enabled") or not tw:
        return None
    with GND_LOCK:
        if GND["icao"] != tw["code"]:
            GND.update(icao=tw["code"], state="loading", note="Loading taxiways...", g=None, src="")
            threading.Thread(target=gnd_load, args=(dict(tw),), daemon=True).start()
            return None
        if GND["state"] != "ready":
            return None
        return {"tw": tw, "g": GND["g"]}


def gnd_load(tw, force=False):
    code = tw["code"]
    path = os.path.join(DATA, f"ground_{code}.json")
    g, note = None, ""
    try:
        old_v = 0
        if os.path.exists(path) and not force:
            try:
                with open(path, encoding="utf-8") as f:
                    old_v = json.load(f).get("nl_v", 1)
            except Exception:
                old_v = 0
        if force or not os.path.exists(path) or old_v < 3 or time.time() - os.path.getmtime(path) > 30 * 86400:
            log(f"Ground: downloading taxiways and gates for {code} from OpenStreetMap...")
            q = (f'[out:json][timeout:180];(way["aeroway"~"^(taxiway|taxilane|runway|apron|terminal|hangar|parking_position|jet_bridge|holding_position)$"](around:5000,{tw["lat"]},{tw["lon"]});'
                 f'way["landuse"="grass"](around:3500,{tw["lat"]},{tw["lon"]});'
                 f'node["aeroway"~"^(gate|parking_position|holding_position)$"](around:5000,{tw["lat"]},{tw["lon"]});'
                 f'way["building"]["aeroway"](around:5000,{tw["lat"]},{tw["lon"]}););out body;>;out skel qt;')
            blob, errs = None, []
            for url_ in OVERPASS_MIRRORS:                      # try several OpenStreetMap servers (they are often busy)
                try:
                    req = urllib.request.Request(url_, data=urllib.parse.urlencode({"data": q}).encode(),
                                                 headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
                    with urllib.request.urlopen(req, timeout=200) as r:
                        blob = r.read()
                    if b'"elements"' not in blob[:4000]:
                        raise ValueError("busy")
                    log(f"Ground: airport map from {url_.split('/')[2]}")
                    break
                except Exception as e_:
                    errs.append(f"{url_.split('/')[2]}: {str(e_)[:60]}")
                    blob = None
            if blob is None:
                raise RuntimeError("; ".join(errs))
            osm_ = json.loads(blob)
            osm_["nl_v"] = 3
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(osm_, f)
            os.replace(path + ".tmp", path)
        with open(path, encoding="utf-8") as f:
            g = gnd_build(json.load(f), tw)
        if not g:
            note = "OpenStreetMap has too little taxiway data here - using a simple layout"
    except Exception as e:
        note = f"Taxiway download failed ({str(e)[:80]}) - using a simple layout"
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    g = gnd_build(json.load(f), tw)
                note = "Airport map servers were busy - using the saved taxiways (aprons/buildings missing). Try 'Reload taxiways' later."
                log(f"Ground: download failed: {e}")
            except Exception:
                g = None
    if not g:
        g = gnd_simple(tw)
    with GND_LOCK:
        if GND["icao"] == code:
            GND.update(state="ready", g=g, src=g["src"], note=note or f"{len(g['gates'])} gates, {len(g['labels'])} named taxiways (OpenStreetMap)")
    log(f"Ground {code}: " + GND["note"])


def gnd_build(osm, tw):
    els = osm.get("elements", [])
    pos = {e["id"]: (e["lat"], e["lon"]) for e in els if e["type"] == "node" and "lat" in e}
    adj, rwyn, labels, gates = {}, {}, [], []

    def edge(a, b, name, k):
        d = m_dist(*pos[a], *pos[b])
        adj.setdefault(a, []).append((b, d * k, name))
        adj.setdefault(b, []).append((a, d * k, name))
    for e in els:
        tags = e.get("tags") or {}
        aw = tags.get("aeroway")
        if e["type"] == "node" and aw in ("gate", "parking_position") and "lat" in e:
            gates.append([(tags.get("ref") or tags.get("name") or "").strip().upper(), e["lat"], e["lon"]])
        if e["type"] != "way":
            continue
        nds = [n for n in e.get("nodes", []) if n in pos]
        if len(nds) < 1:
            continue
        if aw in ("taxiway", "taxilane"):
            name = (tags.get("ref") or tags.get("name") or "").strip().upper()
            if len(name) > 6:
                name = ""
            for a, b in zip(nds, nds[1:]):
                edge(a, b, name, 1.0 if aw == "taxiway" else 1.4)
            if name and len(nds) >= 2:
                mid = pos[nds[len(nds) // 2]]
                labels.append([round(mid[0], 6), round(mid[1], 6), name])
        elif aw == "runway":
            ref = (tags.get("ref") or "RWY").strip().upper()
            for n in nds:
                rwyn.setdefault(n, set()).add(ref)
            for a, b in zip(nds, nds[1:]):
                edge(a, b, "RWY " + ref, 25.0)
        elif aw == "parking_position":
            p = pos[nds[0]]
            gates.append([(tags.get("ref") or tags.get("name") or "").strip().upper(), p[0], p[1]])
    if len(adj) < 20:
        return None
    # largest connected piece of the network
    seen, best = set(), set()
    for n in adj:
        if n in seen:
            continue
        comp, stack = {n}, [n]
        while stack:
            x = stack.pop()
            for y, _, _ in adj[x]:
                if y not in comp:
                    comp.add(y); stack.append(y)
        seen |= comp
        if len(comp) > len(best):
            best = comp
    twn = [n for n in best if n not in rwyn]
    if len(twn) < 10:
        return None
    g = {"src": "osm", "pos": pos, "adj": adj, "rwyn": rwyn, "comp": best, "twn": twn, "labels": labels[:800], "gates": [],
         "diagram": gnd_diagram(els, pos)}
    names = set()
    for i, (name, la, lo) in enumerate(gates):
        if m_dist(la, lo, tw["lat"], tw["lon"]) > 5000:
            continue
        n, d = gnd_nearest(g, la, lo)
        if d > 400:
            continue
        name = name or f"P{i + 1}"
        if name in names:
            continue
        names.add(name)
        g["gates"].append({"name": name, "lat": la, "lon": lo, "node": n})
    if not g["gates"]:      # no gates mapped: use taxiway ends far from the runways as stands
        far = sorted(twn, key=lambda n: -min((m_dist(*pos[n], *pos[r]) for r in list(rwyn)[:200]), default=0))[:24]
        g["gates"] = [{"name": f"R{i + 1}", "lat": pos[n][0], "lon": pos[n][1], "node": n} for i, n in enumerate(far)]
    return g


def gnd_diagram(els, pos):
    """Vector airport diagram for the Ground window (like a ground radar / airport chart)."""
    r6 = lambda p: [round(p[0], 6), round(p[1], 6)]
    d = {"rwy": [], "twy": [], "apron": [], "bldg": [], "hold": [], "stand": [], "bridge": [], "grass": [], "holdl": []}
    for e in els:
        tags = e.get("tags") or {}
        aw = tags.get("aeroway")
        if e["type"] == "node" and aw == "holding_position" and "lat" in e:
            d["hold"].append([round(e["lat"], 6), round(e["lon"], 6)])
            continue
        if e["type"] != "way":
            continue
        pts = [r6(pos[n]) for n in e.get("nodes", []) if n in pos]
        if len(pts) < 2:
            continue
        w = num(re.sub(r"[^0-9.]", "", tags.get("width", "") or "") or 0)
        if aw == "runway":
            d["rwy"].append([pts, w or 45, (tags.get("ref") or "").upper()])
        elif aw in ("taxiway", "taxilane"):
            d["twy"].append([pts, w or (23 if aw == "taxiway" else 15), (tags.get("ref") or "").upper()])
        elif aw == "apron":
            d["apron"].append([pts, (tags.get("name") or tags.get("ref") or "").upper()[:30]])
        elif aw == "parking_position":
            d["stand"].append([pts, (tags.get("ref") or tags.get("name") or "").upper()[:8]])
        elif aw == "jet_bridge":
            d["bridge"].append(pts)
        elif aw == "holding_position":
            d["holdl"].append(pts)
        elif tags.get("landuse") == "grass":
            if len(pts) <= 400:
                d["grass"].append(pts)
        elif aw in ("terminal", "hangar") or tags.get("building"):
            d["bldg"].append(pts)
    return d


def gnd_simple(tw):
    gates = []
    for i in range(16):
        la, lo = gc_move(tw["lat"], tw["lon"], i * 22.5, 0.35)
        gates.append({"name": f"S{i + 1}", "lat": la, "lon": lo, "node": None})
    return {"src": "simple", "gates": gates, "labels": [], "rwyn": {}, "twn": [], "pos": {}, "adj": {}}


def gnd_nearest(g, lat, lon, pool=None):
    best, bd = None, 1e18
    k = math.cos(math.radians(lat))
    for n in (pool if pool is not None else g["twn"]):
        p = g["pos"][n]
        d = (p[0] - lat) ** 2 + ((p[1] - lon) * k) ** 2
        if d < bd:
            best, bd = n, d
    return best, (math.sqrt(bd) * 111195.0 if best is not None else 1e9)


def gnd_hold_point(gx, end):
    g = gx["g"]
    if g["src"] == "osm":
        cands = [n for n in g["twn"] if 40 < m_dist(*g["pos"][n], end["lat"], end["lon"]) < 450]
        if cands:
            n, _ = gnd_nearest(g, end["lat"], end["lon"], cands)
            return g["pos"][n]
    la, lo = gc_move(end["lat"], end["lon"], (end["crs"] + 180) % 360, 0.03)
    return gc_move(la, lo, (end["crs"] + 90) % 360, 0.09)


def gnd_rollout(gx, end):
    """Points from the threshold along the runway to a taxiway exit (about 1,800 m down the runway)."""
    g = gx["g"]
    thr = (end["lat"], end["lon"])
    if g["src"] == "osm":
        best = None
        for n, refs in g["rwyn"].items():
            if n not in g["comp"] or not any(end["id"] in r.split("/") for r in refs):
                continue
            p = g["pos"][n]
            d = m_dist(*thr, *p)
            if 1100 < d < 3200:
                exits = [y for y, _, _ in g["adj"].get(n, []) if y not in g["rwyn"]]
                if exits and (not best or abs(d - 1800) < best[0]):
                    best = (abs(d - 1800), p, g["pos"][exits[0]])
        if best:
            return [[thr[0], thr[1], "", ""], [best[1][0], best[1][1], "", ""], [best[2][0], best[2][1], "", ""]]
    la, lo = gc_move(*thr, end["crs"], 1800 / 1852)
    la2, lo2 = gc_move(la, lo, (end["crs"] + 90) % 360, 0.09)
    return [[thr[0], thr[1], "", ""], [la, lo, "", ""], [la2, lo2, "", ""]]


def gnd_route(gx, a, b, via=()):
    """Taxi route from point a to point b as [[lat, lon, taxiway name, runway crossed or ''], ...]."""
    g = gx["g"]
    if g["src"] != "osm":
        return [[a[0], a[1], "", ""], [b[0], b[1], "", ""]]
    s, _ = gnd_nearest(g, *a)
    t, _ = gnd_nearest(g, *b)
    via = set(via)
    import heapq
    dist, prev, pq = {s: 0.0}, {}, [(0.0, s)]
    while pq:
        d, x = heapq.heappop(pq)
        if x == t:
            break
        if d > dist.get(x, 1e18):
            continue
        for y, w, name in g["adj"][x]:
            if via and name not in via and not name.startswith("RWY"):
                w *= 3.0
            nd = d + w
            if nd < dist.get(y, 1e18):
                dist[y], prev[y] = nd, (x, name)
                heapq.heappush(pq, (nd, y))
    if t not in dist:
        return [[a[0], a[1], "", ""], [b[0], b[1], "", ""]]
    seq, x = [], t
    while x != s:
        px, name = prev[x]
        seq.append((x, name)); x = px
    seq.append((s, ""))
    seq.reverse()
    pts = [[a[0], a[1], "", ""]]
    for n, name in seq:
        p = g["pos"][n]
        pts.append([p[0], p[1], name, "|".join(sorted(g["rwyn"][n])) if n in g["rwyn"] else ""])
    pts.append([b[0], b[1], pts[-1][2], ""])
    return pts


def gnd_route_seq(gx, a, b, via):
    """Route that follows the named taxiways in order (e.g. B, C1, A). Short unnamed links are allowed between them,
    other taxiways only as a last resort. Returns (points, fully_followed)."""
    g = gx["g"]
    via = [v.upper() for v in via if v]
    if g["src"] != "osm" or not via:
        return gnd_route(gx, a, b, via), True
    import heapq
    n = len(via)
    s0, _ = gnd_nearest(g, *a)
    t, _ = gnd_nearest(g, *b)
    start = (s0, 0)
    dist, prev, pq = {start: 0.0}, {}, [(0.0, s0, 0)]
    goal = None
    best_k = (0, start)
    while pq:
        d, x, k = heapq.heappop(pq)
        if d > dist.get((x, k), 1e18):
            continue
        if k > best_k[0]:
            best_k = (k, (x, k))
        if x == t and k == n:
            goal = (x, k)
            break
        for y, w, name in g["adj"][x]:
            if k < n and name == via[k]:
                nk, ww = k + 1, w
            elif k > 0 and name == via[k - 1]:
                nk, ww = k, w
            elif not name:
                nk, ww = k, w * 1.5
            elif name.startswith("RWY"):
                nk, ww = k, w
            else:
                nk, ww = k, w * 20
            nd = d + ww
            if nd < dist.get((y, nk), 1e18):
                dist[(y, nk)] = nd
                prev[(y, nk)] = ((x, k), name)
                heapq.heappush(pq, (nd, y, nk))
    full = goal is not None
    if not full:
        return gnd_route(gx, a, b, via), False
    seq, st = [], goal
    while st != start:
        pst, name = prev[st]
        seq.append((st[0], name)); st = pst
    seq.append((s0, ""))
    seq.reverse()
    pts = [[a[0], a[1], "", ""]]
    for nd_, name in seq:
        p = g["pos"][nd_]
        pts.append([p[0], p[1], name, "|".join(sorted(g["rwyn"][nd_])) if nd_ in g["rwyn"] else ""])
    pts.append([b[0], b[1], pts[-1][2], ""])
    return pts, True


def gnd_plan(gx, g, target, via):
    """Destination and route for a ground flight (used by TAXI and by the route builder preview)."""
    tw = gx["tw"]
    if g["kind"] == "dep":
        end = tw["dep"] if not target else next((e for e in tw["ends"] if e["id"].lstrip("0") == target.lstrip("0")), None)
        if not end:
            return None, f"Runway {target} not found at {tw['code']}"
        goal, dest = gnd_hold_point(gx, end), ("rwy", end["id"])
    else:
        gate = gnd_pick_gate(gx, target) if target else next((x for x in gx["g"]["gates"] if x["name"] == g["gate"]), None)
        if not gate:
            return None, f"Gate {target} not found"
        goal, dest = (gate["lat"], gate["lon"]), ("gate", gate["name"])
    if via:
        pts, full = gnd_route_seq(gx, (g["lat"], g["lon"]), goal, via)
    else:
        pts, full = gnd_route(gx, (g["lat"], g["lon"]), goal, via), True
    return {"pts": pts, "full": full, "dest": dest}, None


def api_ground_preview(body):
    gx = ground_ctx()
    if not gx:
        return {"ok": False, "message": "Ground control is off"}, 400
    with AI.lock:
        f = next((fl for fl in AI.flights if fl["id"] == body.get("id")), None)
        if not f or not f.get("gnd"):
            return {"ok": False, "message": "Select a plane that is with Ground"}, 400
        g = f["gnd"]
        via = [str(v).upper() for v in (body.get("via") or [])][:20]
        plan, err = gnd_plan(gx, g, str(body.get("to") or "").upper(), via)
    if err:
        return {"ok": False, "message": err}, 400
    xs = []
    for p in plan["pts"]:
        if p[3] and p[3].replace("|", " and ") not in xs:
            xs.append(p[3].replace("|", " and "))
    return {"ok": True, "path": [[round(p[0], 6), round(p[1], 6)] for p in plan["pts"]], "names": twy_names(plan["pts"]),
            "xpts": [[round(p[0], 6), round(p[1], 6)] for p in plan["pts"] if p[3]],
            "full": plan["full"], "dest": plan["dest"], "cross": xs, "kind": g["kind"], "cs": f["leg"]["cs"],
            "message": "" if plan["full"] else "Those taxiways don't connect in that order - showing the nearest possible route"}, 200


def twy_names(pts):
    out = []
    for p in pts:
        n = p[2]
        if n and not n.startswith("RWY") and (not out or out[-1] != n):
            out.append(n)
    return out


def gnd_pick_gate(gx, want=None):
    busy = {fl["gnd"].get("gate") for fl in AI.flights if fl.get("gnd")}
    gates = gx["g"]["gates"]
    if want:
        return next((x for x in gates if x["name"] == want), None)
    free = [x for x in gates if x["name"] not in busy] or gates
    return random.choice(free)


def gnd_new_dep(f, now, gx):
    leg = f["leg"]
    gate = gnd_pick_gate(gx)
    tw = gx["tw"]
    trk = bearing(gate["lat"], gate["lon"], tw["lat"], tw["lon"])
    f["gnd"] = {"leg": leg["t0"], "kind": "dep", "st": "gate", "lat": gate["lat"], "lon": gate["lon"], "trk": trk,
                "gate": gate["name"], "path": [], "i": 1, "t": now, "gs": 0.0, "rwy": tw["dep"]["id"], "clr": [], "xr": ""}
    comm(leg["cs"], f"Ground, {spoken(leg['cs'])}, gate {gate['name']}, ready to push and start", f["id"], pk("ground"))


def gnd_new_arr(f, c, now, gx, end):
    leg = f["leg"]
    gate = gnd_pick_gate(gx)
    f["gnd"] = {"leg": None, "kind": "arr", "st": "roll", "lat": end["lat"], "lon": end["lon"], "trk": end["crs"],
                "gate": gate["name"], "path": gnd_rollout(gx, end), "i": 1, "t": now, "gs": max(110.0, c["gs"]),
                "rwy": end["id"], "clr": [], "xr": ""}


def gnd_step(f, g, dt, now):
    leg = f["leg"]; cs = leg["cs"]
    st = g["st"]
    if st not in ("push", "taxi", "roll"):
        g["gs"] = 0.0
        return
    spd = PUSH_KT if st == "push" else TAXI_KT if st == "taxi" else max(20.0, g["gs"] - 3.5 * dt)
    g["gs"] = spd
    left = spd * 0.514444 * dt
    pts = g["path"]
    while left > 0 and g["i"] < len(pts):
        nx = pts[g["i"]]
        d = m_dist(g["lat"], g["lon"], nx[0], nx[1])
        stop_at = None
        if st == "taxi" and nx[3] and not all(r in g["clr"] for r in nx[3].split("|")):
            stop_at = max(0.0, d - 75.0)
            if stop_at <= 0.5:
                g["st"] = "hs_rwy"; g["xr"] = nx[3]; g["gs"] = 0.0
                comm(cs, f"{spoken(cs)}, holding short runway {nx[3].replace('|', ' and ')}", f["id"], pk("ground"))
                return
        lim = d if stop_at is None else stop_at
        if d > 0.01 and st != "push":
            g["trk"] = bearing(g["lat"], g["lon"], nx[0], nx[1])
        if left < lim:
            f_ = left / d if d > 0 else 1
            g["lat"] += (nx[0] - g["lat"]) * f_
            g["lon"] += (nx[1] - g["lon"]) * f_
            return
        if stop_at is not None:
            fr = stop_at / d if d > 0 else 0
            g["lat"] += (nx[0] - g["lat"]) * fr; g["lon"] += (nx[1] - g["lon"]) * fr
            left = 0
            continue
        g["lat"], g["lon"] = nx[0], nx[1]
        left -= d
        g["i"] += 1
    if g["i"] >= len(pts):
        gnd_arrived(f, g, now)


def gnd_arrived(f, g, now):
    leg = f["leg"]; cs = leg["cs"]
    g["gs"] = 0.0
    if g["st"] == "push":
        g["st"] = "pushed"
        comm(cs, f"{spoken(cs)}, push back complete, ready to taxi", f["id"], pk("ground"))
    elif g["st"] == "roll":
        g["st"] = "exit"
        release_rwy(cs)
        comm(cs, f"Ground, {spoken(cs)}, clear of runway {g['rwy']}, request taxi to gate", f["id"], pk("ground"))
    elif g["st"] == "taxi" and g["kind"] == "dep":
        f.pop("gnd", None)
        f["gnd_done"] = leg["t0"]
        f["twr"] = {"state": "holding", "rwy": g["rwy"], "since": now, "pos": [g["lat"], g["lon"], g["trk"]]}
        comm(cs, f"Tower, {spoken(cs)}, holding short runway {g['rwy']}, ready for departure", f["id"], pk("tower"))
    elif g["st"] == "taxi":
        f.pop("gnd", None)
        f["gnd_done"] = leg["t0"]
        comm(cs, f"{cs} is parked at gate {g['gate']}", f["id"], "event")


def gnd_state(f, now):
    g = f["gnd"]
    gx = ground_ctx()
    if not gx:                         # Ground switched off: the flight carries on by itself
        f.pop("gnd", None)
        f["gnd_done"] = f["leg"]["t0"]
        return None
    if now - g["t"] > 3600:
        g["t"] = now - 3600
    if not mine("ground"):
        ai_ground(f, g, now, gx)
        if f.get("gnd") is not g:
            return AI.tower_hold_state(f) if f.get("twr") else None
    while g["t"] < now - 1e-6 and f.get("gnd") is g:
        dt = min(1.0, now - g["t"])
        gnd_step(f, g, dt, g["t"] + dt)
        g["t"] += dt
    elev = gx["tw"]["elev"]
    if f.get("gnd") is not g:
        return AI.tower_hold_state(f) if f.get("twr") else None
    phase = {"gate": f"At gate {g['gate']}", "push": "Pushing back", "pushed": "Ready to taxi",
             "taxi": "Taxiing" + (" to runway " + g["rwy"] if g["kind"] == "dep" else " to gate " + g["gate"]),
             "held": "Holding position", "hs_rwy": f"Holding short runway {g['xr']}", "roll": f"Landing roll runway {g['rwy']}",
             "exit": f"Clear of runway {g['rwy']}, waiting for taxi"}.get(g["st"], "On ground")
    return {"phase": "GND: " + phase, "lat": g["lat"], "lon": g["lon"], "alt": elev, "gs": round(g["gs"]), "vs": 0, "trk": g["trk"],
            "ground": True, "off_block": None, "eta": None, "dist": None}


def ai_ground(f, g, now, gx):
    if g.get("st_seen") != g["st"]:
        g["st_seen"], g["st_t"] = g["st"], now
    wait = now - g["st_t"]
    st = g["st"]
    cmd = None
    if st == "gate" and wait > g.setdefault("ai_wait", random.uniform(25, 120)):
        cmd = ["PB"]
    elif st == "pushed" and wait > 8:
        cmd = ["TAXI"]
    elif st == "exit" and wait > 4:
        cmd = ["TAXI"]
    elif st == "held" and wait > 20:
        cmd = ["CONT"]
    elif st == "hs_rwy" and wait > 4:
        refs = set(g["xr"].replace("|", "/").split("/"))
        busy = any(ap == gx["tw"]["code"] and set(k.split("/")) & refs and (v["until"] is None or v["until"] > now) for (ap, k), v in RWY_OCC.items())
        final = False
        for h in AI.flights:
            c = h.get("ctl")
            if c and c["mode"] == "ils" and c.get("ils") and set(phys(c["ils"]).split("/")) & refs:
                if dist_nm(c["lat"], c["lon"], c["ils"]["lat"], c["ils"]["lon"]) < 4:
                    final = True
        if not busy and not final:
            cmd = ["CROSS"]
    if cmd:
        with ai_voice():
            ground_cmd(f, cmd, now)


def ground_cmd(f, toks, now):
    gx = ground_ctx()
    g = f["gnd"]; leg = f["leg"]; cs = leg["cs"]
    if not gx:
        return False, "Ground control is off"
    tw = gx["tw"]
    act, target, via, bad, mode = None, None, [], [], None
    for t in toks:
        if mode == "via":
            if t in ("CROSS", "HOLD", "TAXI"):
                mode = None
            else:
                via.append(t); continue
        if t in ("PB", "PUSH", "PUSHBACK"):
            act = "pb"
        elif t in ("TAXI", "TX", "T"):
            act = "taxi"
        elif t == "VIA":
            mode = "via"
        elif t in ("HOLD", "HP", "STOP"):
            act = "hold"
        elif t in ("CONT", "CONTINUE", "GO"):
            act = "cont"
        elif t in ("CROSS", "CR", "X"):
            act = act or "cross"
        elif t in ("GATE", "RWY", "RUNWAY", "TO"):
            pass
        elif act == "taxi" and target is None:
            target = t
        elif act == "cross":
            pass
        else:
            bad.append(t)
    if not act:
        hint = "PB (push back), TAXI [runway] [VIA A B], HOLD, CONT, CROSS" if g["kind"] == "dep" else "TAXI [gate] [VIA A B], HOLD, CONT, CROSS"
        return False, f"{cs} is with Ground ({g['st']}): use {hint}" + (f". Not understood: {' '.join(bad)}" if bad else "")
    gs_ = lambda txt: comm("GND", f"{spoken(cs)}, {txt}", f["id"], "atc")
    rb = lambda txt: comm(cs, f"{txt}, {spoken(cs)}", f["id"], "pilot")
    if act == "pb":
        if g["st"] != "gate":
            return False, f"{cs} is not at the gate"
        gate = next((x for x in gx["g"]["gates"] if x["name"] == g["gate"]), None)
        to = gx["g"]["pos"][gate["node"]] if gate and gate.get("node") is not None else (tw["lat"], tw["lon"])
        brg = bearing(g["lat"], g["lon"], to[0], to[1])
        la, lo = gc_move(g["lat"], g["lon"], brg, 45 / 1852)
        g.update(st="push", path=[[g["lat"], g["lon"], "", ""], [la, lo, "", ""]], i=1, trk=(brg + 180) % 360)
        gs_("push back approved"); rb("pushing back")
        return True, "push back approved"
    if act == "hold":
        if g["st"] != "taxi":
            return False, f"{cs} is not taxiing"
        g["st"] = "held"; gs_("hold position"); rb("holding position")
        return True, "hold position"
    if act == "cont":
        if g["st"] != "held":
            return False, f"{cs} is not holding"
        g["st"] = "taxi"; gs_("continue taxi"); rb("continuing")
        return True, "continue taxi"
    if act == "cross":
        if g["st"] != "hs_rwy":
            return False, f"{cs} is not holding short of a runway"
        r = g["xr"].replace("|", " and ")
        g["clr"].extend(g["xr"].split("|")); g["st"] = "taxi"
        gs_(f"cross runway {r}"); rb(f"crossing runway {r}")
        for (ap, k), v in RWY_OCC.items():
            if ap == tw["code"] and set(k.split("/")) & set(g["xr"].replace("|", "/").split("/")) and (v["until"] is None or v["until"] > now):
                comm("NIGHTLOOP", f"Warning: runway {r} is occupied by {v['cs']}", f["id"], "unable")
        return True, f"cross runway {r}"
    # taxi
    if g["st"] in ("push", "roll"):
        return False, f"{cs} can't taxi yet ({'pushing back' if g['st'] == 'push' else 'still rolling out'})"
    plan, err = gnd_plan(gx, g, target, via)
    if err:
        return False, err
    if plan["dest"][0] == "rwy":
        g["rwy"] = plan["dest"][1]
    else:
        g["gate"] = plan["dest"][1]
    dest_txt = ("runway " if plan["dest"][0] == "rwy" else "gate ") + plan["dest"][1]
    pts = plan["pts"]
    g.update(st="taxi", path=pts, i=1, clr=[], xr="")
    names = twy_names(pts)
    xs = []
    for p in pts:
        if p[3] and p[3].replace("|", " and ") not in xs:
            xs.append(p[3].replace("|", " and "))
    txt = f"taxi to {dest_txt}" + (f" via {' '.join(names)}" if names else "") + (f", hold short runway {xs[0]}" if xs else "")
    gs_(txt)
    rb(f"{dest_txt}" + (f" via {' '.join(names)}" if names else "") + (f", hold short {xs[0]}" if xs else ""))
    return True, txt


def api_ground_diagram(q):
    gx = ground_ctx()
    if not gx or not gx["g"].get("diagram"):
        return {"airport": GND["icao"], "diagram": None}
    return {"airport": GND["icao"], "diagram": gx["g"]["diagram"]}


def api_ground(q):
    gc = CFG.get("ground") or {}
    gx = ground_ctx()
    out = {"enabled": bool(gc.get("enabled")), "tower_on": bool(tower_ctx()), "state": GND["state"], "note": GND["note"],
           "src": GND["src"], "airport": GND["icao"], "labels": [], "gates": [], "flights": []}
    if not gx:
        return out
    out["labels"] = gx["g"]["labels"]
    out["gates"] = [[x["name"], round(x["lat"], 6), round(x["lon"], 6)] for x in gx["g"]["gates"]]
    with AI.lock:
        for f in AI.flights:
            g = f.get("gnd")
            if g:
                out["flights"].append({"id": f["id"], "cs": f["leg"]["cs"], "type": f["leg"]["type"], "kind": g["kind"], "st": g["st"],
                                       "gate": g["gate"], "rwy": g["rwy"], "xr": g["xr"], "to": f["leg"]["arr"], "from": f["leg"]["dep"],
                                       "via": twy_names(g["path"][g["i"] - 1:]) if g["st"] in ("taxi", "held", "hs_rwy") else []})
    order = {"hs_rwy": 0, "exit": 1, "gate": 2, "pushed": 3, "held": 4, "taxi": 5, "push": 6, "roll": 7}
    out["flights"].sort(key=lambda x: order.get(x["st"], 9))
    return out


def set_ground(body):
    gc = CFG.setdefault("ground", {"enabled": True})
    if "enabled" in body:
        gc["enabled"] = bool(body["enabled"])
        save_config()
        if not gc["enabled"]:
            with AI.lock:
                for f in AI.flights:
                    if f.get("gnd"):
                        f.pop("gnd"); f["gnd_done"] = f["leg"]["t0"]
    if body.get("reload"):
        tw = tower_ctx()
        if tw:
            with GND_LOCK:
                GND.update(icao=tw["code"], state="loading", note="Downloading taxiways again...", g=None)
            threading.Thread(target=gnd_load, args=(dict(tw), True), daemon=True).start()
    log(f"Ground: {gc}")
    return api_ground({}), 200


# ---------------------------------------------------------------- Voice commands (speech -> typed command)

DIGITS = {"zero": "0", "oh": "0", "one": "1", "won": "1", "two": "2", "to": None, "too": "2", "three": "3", "tree": "3", "four": "4",
          "for": None, "fower": "4", "five": "5", "fife": "5", "six": "6", "seven": "7", "eight": "8", "ate": "8", "nine": "9", "niner": "9"}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
         "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
PHONETIC = {"alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E", "foxtrot": "F", "golf": "G",
            "hotel": "H", "india": "I", "juliet": "J", "juliett": "J", "kilo": "K", "lima": "L", "mike": "M", "november": "N",
            "oscar": "O", "papa": "P", "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
            "whiskey": "W", "whisky": "W", "xray": "X", "yankee": "Y", "zulu": "Z"}


TELEPHONY_WORDS = {v.lower() for v in TELEPHONY.values()}


def voice_norm(text):
    """Lower-case words; spoken numbers become digits ('two seven zero' -> 270, 'one one thousand five hundred' -> 11500);
    phonetic letters become '#a'."""
    t = text.lower().replace("-", " ").replace("x ray", "xray").replace("take off", "takeoff").replace("push back", "pushback")
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)
    t = re.sub(r"[^a-z0-9# ]", " ", t)
    words = t.split()
    out, cur, total, isnum = [], "", 0, False

    def flush():
        nonlocal cur, total, isnum
        if isnum:
            out.append(str(total + (int(cur) if cur else 0)) if total else cur)
        cur, total, isnum = "", 0, False
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w.isdigit():
            if total and len(w) <= 2 and not cur:
                cur = w
            else:
                cur += w
            isnum = True
        elif w in DIGITS and DIGITS[w]:
            cur += DIGITS[w]; isnum = True
        elif w in TEENS:
            v = TEENS[w]
            if v >= 20 and (DIGITS.get(nxt) or "") and nxt not in ("oh",):
                cur += str(v)[0]      # 'forty five' -> '4' then '5'
            else:
                cur += str(v)
            isnum = True
        elif w in ("thousand", "hundred") and isnum:
            total += int(cur or "1") * (1000 if w == "thousand" else 100)
            cur = ""
        elif w in PHONETIC and not (w in TELEPHONY_WORDS and (nxt.isdigit() or nxt in DIGITS or nxt in TEENS)):
            flush(); out.append("#" + PHONETIC[w].lower())
        else:
            flush(); out.append(w)
    flush()
    return " ".join(out)


def voice_callsign(s, flights):
    """Find '<airline telephony> <number>' (or ICAO letters + number) at the start of the phrase."""
    names = sorted(((v.lower(), k) for k, v in TELEPHONY.items()), key=lambda x: -len(x[0]))
    for spoken_name, code in names:
        m = re.search(r"\b" + re.escape(spoken_name) + r" (\d{1,4})\b", s)
        if m:
            return code + m.group(1), s[:m.start()] + s[m.end():]
    m = re.search(r"((?:#[a-z] ){3})(\d{1,4})\b", s)
    if m:
        return m.group(1).replace("#", "").replace(" ", "").upper() + m.group(2), s[:m.start()] + s[m.end():]
    return None, s


SIDE = {"left": "L", "right": "R", "center": "C", "centre": "C", "#l": "L", "#r": "R", "#c": "C"}
RWY_RE = r"(\d{1,2})(?: (left|right|center|centre|#l|#r|#c))?"


def letters(txt):
    """'#a 5 #b' -> ['A5', 'B'] (taxiway / gate names)."""
    out = []
    for tok in txt.split():
        if tok.startswith("#"):
            out.append(tok[1:].upper())
        elif tok.isdigit() and out:
            out[-1] += tok
        elif tok.isdigit():
            out.append(tok)
    return out


def voice_to_cmd(text):
    s = " " + voice_norm(text) + " "
    cs, s = voice_callsign(s, None)
    taken, found = [], []

    def add(m, cmd):
        a, b = m.span()
        if any(a < y and b > x for x, y in taken):
            return
        taken.append((a, b)); found.append((a, cmd))
    rw = lambda num_, side: num_.lstrip("0") .zfill(2 if len(num_) > 1 else 1) + (SIDE.get(side or "", ""))
    pats = [
        (r"cleared (?:for )?takeoff|clear for takeoff", lambda m: "CTO"),
        (r"cleared to land|clear to land", lambda m: "CL"),
        (r"line up and wait|line up", lambda m: "LUAW"),
        (r"go around", lambda m: "GA"),
        (r"resume (?:normal )?speed|normal speed", lambda m: "SR"),
        (r"(?:resume )?own navigation|resume navigation", lambda m: "RON"),
        (r"pushback(?: approved)?", lambda m: "PB"),
        (r"hold position", lambda m: "HOLD"),
        (r"continue taxi|continue", lambda m: "CONT"),
        (r"cross runway " + RWY_RE + "|cross", lambda m: "CROSS"),
        (r"taxi(?: to)?(?: runway " + RWY_RE + r")?(?: (?:to )?(?:the )?(?:gate|stand|parking)((?: (?:#[a-z]|\d+))+))?(?: via((?: (?:#[a-z]|\d+))+))?",
         lambda m: " ".join(["TAXI"] + ([rw(m.group(1), m.group(2))] if m.group(1) else [])
                            + ([("".join(letters(m.group(3))))] if m.group(3) else [])
                            + (["VIA"] + letters(m.group(4)) if m.group(4) else []))),
        (r"(?:cleared )?(?:ils|i l s|localizer|cleared approach|approach)(?: approach)?(?: runway)? " + RWY_RE, lambda m: "ILS " + rw(m.group(1), m.group(2))),
        (r"(?:contact|monitor) (?:\w+ )?(?:approach|center|centre|departure|tower|radar)|hand ?off|frequency change approved", lambda m: "HO"),
        (r"turn (left|right)(?: heading)? (\d{1,3})", lambda m: ("L" if m.group(1) == "left" else "R") + m.group(2).zfill(3)),
        (r"(?:fly |maintain |)heading (\d{1,3})", lambda m: "H" + m.group(1).zfill(3)),
        (r"(?:climb|descend)(?: and)?(?: maintain)?(?: to)? (?:flight level|fl) (\d{2,3})", lambda m: "FL" + m.group(1).zfill(3)),
        (r"(?:climb|descend)(?: and)?(?: maintain)?(?: to)? (\d{3,5})(?: feet)?", lambda m: "A" + m.group(1)),
        (r"(?:maintain )?(?:flight level|fl) (\d{2,3})", lambda m: "FL" + m.group(1).zfill(3)),
        (r"(?:reduce|increase|maintain|slow|speed)(?: speed)?(?: to)? (\d{2,3})(?: knots)?|(\d{2,3}) knots", lambda m: "S" + (m.group(1) or m.group(2))),
        (r"maintain (\d{4,5})(?: feet)?", lambda m: "A" + m.group(1)),
        (r"(?:proceed )?direct(?: to)? ((?:#[a-z] ?){2,5}|[a-z]{3,5}\b)", lambda m: "D " + m.group(1).replace("#", "").replace(" ", "").upper()),
    ]
    for rx, fn in pats:
        for m in re.finditer(r"(?<![a-z0-9#])(?:" + rx + r")(?![a-z0-9])", s):
            add(m, fn(m))
    found.sort()
    cmd = " ".join(c for _, c in found)
    return cs, cmd


# ---- offline speech recognition (Vosk): works in the standalone Windows app and on the Raspberry Pi, no internet needed
VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VLOCAL = {"rec": None, "stream": None, "buf": None, "partial": "", "model": None, "lock": threading.Lock(), "installing": False, "msg": ""}


def vosk_dir():
    return os.path.join(DATA, "vosk-model")


def voice_local_info(q=None):
    try:
        import vosk  # noqa: F401
        lib = True
    except Exception as e:
        lib, VLOCAL["msg"] = False, f"speech engine not included ({str(e)[:60]})"
    try:
        mics = list_mics()
    except Exception as e:
        mics = []
        if lib:
            VLOCAL["msg"] = f"no microphone access ({str(e)[:60]})"
    model = os.path.exists(os.path.join(vosk_dir(), "am")) or os.path.exists(os.path.join(vosk_dir(), "conf"))
    return {"engine": lib, "model": model, "mics": mics[:12], "mic": CFG.get("voice_mic"), "available": lib and model and bool(mics),
            "installing": VLOCAL["installing"], "message": VLOCAL["msg"], "listening": VLOCAL["stream"] is not None}


def list_mics():
    """Input devices of the main sound system (each microphone once)."""
    import sounddevice as sd
    devs = sd.query_devices()
    try:
        api = sd.default.hostapi if not isinstance(sd.default.hostapi, (list, tuple)) else sd.default.hostapi[0]
    except Exception:
        api = 0
    try:
        dflt = sd.default.device[0]
    except Exception:
        dflt = -1
    out = []
    for i, d in enumerate(devs):
        if d.get("max_input_channels", 0) > 0 and (d.get("hostapi") == api or IS_PI):
            n = d["name"]
            if re.search(r"stereo mix|what u hear|loopback|sound mapper|primary sound capture", n, re.I):
                continue
            out.append({"i": i, "name": n, "default": i == dflt})
    return out


def set_mic(body):
    CFG["voice_mic"] = None if body.get("index") in (None, "", "default") else int(body["index"])
    save_config()
    return {"ok": True, "mic": CFG["voice_mic"]}, 200


def voice_local_install(body):
    if VLOCAL["installing"]:
        return {"ok": True, "message": "Already downloading"}, 200

    def job():
        import io, zipfile
        VLOCAL.update(installing=True, msg="Downloading the offline voice model (40 MB)...")
        try:
            req = urllib.request.Request(VOSK_URL, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=600) as r:
                blob = r.read()
            z = zipfile.ZipFile(io.BytesIO(blob))
            tmp = vosk_dir() + ".tmp"
            shutil.rmtree(tmp, ignore_errors=True)
            z.extractall(tmp)
            inner = [os.path.join(tmp, d) for d in os.listdir(tmp)]
            src = inner[0] if len(inner) == 1 and os.path.isdir(inner[0]) else tmp
            shutil.rmtree(vosk_dir(), ignore_errors=True)
            shutil.move(src, vosk_dir())
            shutil.rmtree(tmp, ignore_errors=True)
            VLOCAL["msg"] = "Offline voice installed"
            VLOCAL["model"] = None
        except Exception as e:
            VLOCAL["msg"] = f"Download failed: {e}"
        VLOCAL["installing"] = False
    threading.Thread(target=job, daemon=True).start()
    return {"ok": True, "message": "Downloading..."}, 200


def voice_grammar():
    words = set("thousand hundred flight level heading turn left right climb descend maintain and to speed knots reduce increase "
                "slow normal resume own navigation direct proceed cleared clear for approach runway center centre land takeoff "
                "take off line up wait go around contact tower ground departure radar approach hand off frequency change approved "
                "push back taxi via hold position continue cross gate stand parking say again feet localizer ils i l s "
                "monitor good day expect vectors fly".split())
    words |= set(k for k in DIGITS) | set(TEENS) | set(PHONETIC)
    for v in TELEPHONY.values():
        words |= set(re.findall(r"[a-z]+", v.lower()))
    return json.dumps(sorted(words) + ["[unk]"])


def voice_local_start(body):
    info = voice_local_info()
    if not info["available"]:
        return {"ok": False, "message": "Offline voice is not ready: " + (info["message"] or "install it in Settings > Voice")}, 400
    import vosk, sounddevice
    with VLOCAL["lock"]:
        if VLOCAL["stream"] is not None:
            return {"ok": True}, 200
        if VLOCAL["model"] is None:
            vosk.SetLogLevel(-1)
            VLOCAL["model"] = vosk.Model(vosk_dir())
        dev = CFG.get("voice_mic")
        try:
            info = sounddevice.query_devices(dev if dev is not None else None, "input")
        except Exception:
            dev, info = None, sounddevice.query_devices(None, "input")
        rate = int(info.get("default_samplerate") or 16000)
        rec = vosk.KaldiRecognizer(VLOCAL["model"], rate, voice_grammar())
        rec.SetMaxAlternatives(3)
        VLOCAL.update(rec=rec, partial="", finals=[], peak=0, level=0, dev_name=info.get("name", "?"))

        def cb(indata, frames, t, status):
            r = VLOCAL["rec"]
            if r is None:
                return
            import array
            a = array.array("h", bytes(indata))
            if a:
                pk = max(abs(min(a)), abs(max(a)))
                VLOCAL["peak"] = max(VLOCAL.get("peak", 0), pk)
                VLOCAL["level"] = min(100, int(pk * 100 / 12000))
            if r.AcceptWaveform(bytes(indata)):
                res = json.loads(r.Result())
                alts = res.get("alternatives") or [{"text": res.get("text", "")}]
                if alts and alts[0].get("text"):
                    VLOCAL["finals"].append(alts)
            else:
                VLOCAL["partial"] = json.loads(r.PartialResult()).get("partial", "")
        try:
            st = sounddevice.RawInputStream(samplerate=rate, blocksize=int(rate / 4), dtype="int16", channels=1, device=dev, callback=cb)
            st.start()
        except Exception as e:
            VLOCAL.update(rec=None)
            return {"ok": False, "message": f"Could not open the microphone '{info.get('name', '?')}': {str(e)[:80]}. Choose another one in Settings > Voice."}, 400
        VLOCAL["stream"] = st
    return {"ok": True}, 200


def voice_local_partial(q):
    heard = " ".join(a[0]["text"] for a in VLOCAL.get("finals", []))
    return {"text": (heard + " " + VLOCAL.get("partial", "")).strip(), "listening": VLOCAL["stream"] is not None,
            "level": VLOCAL.get("level", 0), "mic": VLOCAL.get("dev_name", "")}


def voice_local_stop(body):
    with VLOCAL["lock"]:
        st, rec = VLOCAL["stream"], VLOCAL["rec"]
        VLOCAL.update(stream=None)
        if st is None:
            return {"ok": False, "message": "Not listening"}, 400
        try:
            st.stop(); st.close()
        except Exception:
            pass
        res = json.loads(rec.FinalResult())
        alts = res.get("alternatives") or [{"text": res.get("text", "")}]
        finals = VLOCAL.get("finals", []) + ([alts] if alts and alts[0].get("text") else [])
        peak, mic = VLOCAL.get("peak", 0), VLOCAL.get("dev_name", "?")
        VLOCAL.update(rec=None, partial="", finals=[])
    finals = [f for f in finals if f[0]["text"].strip() not in ("", "[unk]")]
    if body.get("test"):
        return {"ok": True, "peak": peak, "mic": mic, "text": " ".join(f[0]["text"] for f in finals)}, 200
    if not finals:
        if peak < 300:
            return {"ok": False, "message": f"The microphone '{mic}' gave no sound. Choose your microphone in Settings > Voice (Test microphone), "
                                            "and in Windows Settings > Privacy & security > Microphone turn on 'Let desktop apps access your microphone'."}, 400
        return {"ok": False, "message": "Heard sound but no words I know - speak a bit slower, e.g. 'United 4527, turn left heading two seven zero'."}, 400
    first = " ".join(f[0]["text"] for f in finals)
    options = [first]
    if len(finals) == 1:
        options += [a["text"] for a in finals[0][1:] if a.get("text")]
    return api_voice({"alts": options, "id": body.get("id")})


# ---- natural pilot voices: Piper neural text-to-speech, runs offline on Windows and the Raspberry Pi
PIPER_REL = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/"
PIPER_HF = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
PIPER_VOICES = [("ryan", "en/en_US/ryan/medium/en_US-ryan-medium"), ("alan", "en/en_GB/alan/medium/en_GB-alan-medium"),
                ("lessac", "en/en_US/lessac/medium/en_US-lessac-medium")]
TTS = {"installing": False, "msg": "", "lock": threading.Lock()}


def piper_exe():
    d = os.path.join(DATA, "piper")
    for n in ("piper.exe", "piper"):
        p = os.path.join(d, n)
        if os.path.exists(p):
            return p
    return None


def tts_voices():
    vd = os.path.join(DATA, "voices")
    return [n for n, path in PIPER_VOICES if os.path.exists(os.path.join(vd, os.path.basename(path) + ".onnx"))]


def tts_info(q=None):
    return {"installed": bool(piper_exe()) and bool(tts_voices()), "voices": tts_voices(), "installing": TTS["installing"], "message": TTS["msg"]}


def tts_install(body):
    if TTS["installing"]:
        return {"ok": True, "message": "Already downloading"}, 200

    def job():
        import io, zipfile, tarfile, platform
        TTS.update(installing=True)
        try:
            mach = platform.machine().lower()
            pkg = "piper_windows_amd64.zip" if os.name == "nt" else ("piper_linux_aarch64.tar.gz" if mach in ("aarch64", "arm64")
                                                                    else "piper_linux_armv7l.tar.gz" if mach.startswith("arm") else "piper_linux_x86_64.tar.gz")
            if not piper_exe():
                TTS["msg"] = "Downloading the voice engine (25 MB)..."
                blob = urllib.request.urlopen(urllib.request.Request(PIPER_REL + pkg, headers={"User-Agent": UA}), timeout=300).read()
                if pkg.endswith(".zip"):
                    zipfile.ZipFile(io.BytesIO(blob)).extractall(DATA)
                else:
                    tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz").extractall(DATA)
                exe = piper_exe()
                if exe and os.name != "nt":
                    os.chmod(exe, 0o755)
            vd = os.path.join(DATA, "voices")
            os.makedirs(vd, exist_ok=True)
            for i, (name, path) in enumerate(PIPER_VOICES):
                base = os.path.join(vd, os.path.basename(path))
                if os.path.exists(base + ".onnx"):
                    continue
                TTS["msg"] = f"Downloading voice {i + 1} of {len(PIPER_VOICES)} ({name}, 60 MB)..."
                for ext in (".onnx.json", ".onnx"):
                    blob = urllib.request.urlopen(urllib.request.Request(PIPER_HF + path + ext, headers={"User-Agent": UA}), timeout=900).read()
                    with open(base + ext + ".tmp", "wb") as f:
                        f.write(blob)
                    os.replace(base + ext + ".tmp", base + ext)
            TTS["msg"] = "Natural voices installed"
        except Exception as e:
            TTS["msg"] = f"Download failed: {str(e)[:120]}"
        TTS["installing"] = False
    threading.Thread(target=job, daemon=True).start()
    return {"ok": True, "message": "Downloading..."}, 200


def tts_wav(q):
    """WAV bytes for a pilot/controller line (cached)."""
    import hashlib
    exe, voices = piper_exe(), tts_voices()
    if not exe or not voices:
        return None
    text = str(q.get("text", ""))[:400]
    v = q.get("voice") if q.get("voice") in voices else voices[int(q.get("n", 0) or 0) % len(voices)]
    speed = max(0.6, min(1.4, num(q.get("speed"), 1.0)))
    key = hashlib.sha1(f"{v}|{speed:.2f}|{text}".encode()).hexdigest()[:20]
    cd = os.path.join(DATA, "tts_cache")
    os.makedirs(cd, exist_ok=True)
    out = os.path.join(cd, key + ".wav")
    if not os.path.exists(out):
        model = os.path.join(DATA, "voices", os.path.basename(dict(PIPER_VOICES)[v]) + ".onnx")
        with TTS["lock"]:
            r = subprocess.run([exe, "--model", model, "--length_scale", f"{1 / speed:.2f}", "--sentence_silence", "0.1", "--output_file", out],
                               input=text.encode("utf-8"), capture_output=True, timeout=60, cwd=os.path.dirname(exe),
                               creationflags=0x08000000 if os.name == "nt" else 0)
        if r.returncode != 0 or not os.path.exists(out):
            log(f"Voice failed: {r.stderr.decode(errors='ignore')[-200:]}")
            return None
        files = sorted((os.path.join(cd, f) for f in os.listdir(cd)), key=os.path.getmtime)
        for f in files[:-400]:
            try:
                os.remove(f)
            except Exception:
                pass
    with open(out, "rb") as f:
        return f.read()


# ---- "Update all data": downloads every source again
DATA_JOB = {"running": False, "log": [], "t": None}


def data_refresh(body):
    if DATA_JOB["running"]:
        return {"ok": True, "message": "Already updating"}, 200

    def step(msg):
        DATA_JOB["log"].append(time.strftime("%H:%M:%S ") + msg)
        log("Update all data: " + msg)

    def job():
        DATA_JOB.update(running=True, log=[])
        try:
            step("Airports, runways and radio beacons (OurAirports)...")
            FORCE_DL["on"] = True
            with AI.lock:
                load_airports()
            FORCE_DL["on"] = False
            step(f"  done: {len(AIRPORTS):,} airports, {len(NAVAIDS):,} beacons")
            tw = tower_ctx()
            if tw:
                step(f"Airport map of {tw['code']} (taxiways, gates, buildings - OpenStreetMap)...")
                gnd_load(dict(tw), True)
                step("  " + (GND.get("note") or "done"))
            step("Live traffic (VATSIM, IVAO, real-world ADS-B)...")
            refresh_now()
            step("  done")
            step("Flight routes cache cleared")
            try:
                ROUTE_CACHE.clear()
            except Exception:
                pass
            step("All data is up to date.")
        except Exception as e:
            step(f"Stopped: {e}")
        finally:
            FORCE_DL["on"] = False
            DATA_JOB.update(running=False, t=time.time())
    threading.Thread(target=job, daemon=True).start()
    return {"ok": True, "message": "Updating all data..."}, 200


def data_status(q):
    return {"running": DATA_JOB["running"], "log": DATA_JOB["log"][-30:], "last": DATA_JOB["t"]}


# ---- internet updates from GitHub (NIGHTLOOP OS checks by itself; any NIGHTLOOP can use the button)
GH_UPD = {"last": None, "msg": "", "latest": None}


def github_repo():
    return CFG.get("update_github", "vigneshwarnagaraj/nightloop-os" if NL_OS else "") or ""


def github_check(body=None, install=True):
    repo = github_repo() or "vigneshwarnagaraj/nightloop-os"
    try:
        raw = urllib.request.urlopen(urllib.request.Request(f"https://raw.githubusercontent.com/{repo}/main/app/server.py",
                                                            headers={"User-Agent": UA}), timeout=30).read(30000).decode("utf-8", "ignore")
        m = re.search(r'APP, VERSION = "NIGHTLOOP", "([^"]+)"', raw)
        latest = m.group(1) if m else None
        GH_UPD.update(last=time.time(), latest=latest)
        if not latest or vtuple(latest) <= vtuple(VERSION):
            GH_UPD["msg"] = f"Up to date (version {VERSION})"
            return {"ok": True, "message": GH_UPD["msg"], "latest": latest}, 200
        if not install:
            GH_UPD["msg"] = f"Version {latest} is available"
            return {"ok": True, "message": GH_UPD["msg"], "latest": latest}, 200
        import io, zipfile
        blob = urllib.request.urlopen(urllib.request.Request(f"https://codeload.github.com/{repo}/zip/refs/heads/main",
                                                             headers={"User-Agent": UA}), timeout=180).read()
        src = zipfile.ZipFile(io.BytesIO(blob))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for n in src.namelist():
                parts = n.split("/", 2)
                if len(parts) == 3 and parts[1] == "app" and parts[2] and not n.endswith("/") and \
                        (parts[2] == "server.py" or parts[2].startswith("static/") or parts[2] == "os_update.sh"):
                    z.writestr(parts[2], src.read(n))
            z.writestr("VERSION", latest)
        newv = install_package(buf.getvalue())
        GH_UPD["msg"] = f"Installed version {newv} from the internet - restarting"
        schedule_restart()
        return {"ok": True, "message": GH_UPD["msg"], "latest": latest}, 200
    except Exception as e:
        GH_UPD["msg"] = f"Internet update check failed: {str(e)[:100]}"
        return {"ok": False, "message": GH_UPD["msg"]}, 400


def github_auto():
    time.sleep(90)
    while True:
        if github_repo() and CFG.get("auto_update", True):
            github_check()
        time.sleep(6 * 3600)


def api_voice(body):
    alts = [a for a in (body.get("alts") or []) if a][:5]
    if not alts:
        return {"ok": False, "message": "Nothing heard"}, 400
    best = None
    for a in alts:
        cs, cmd = voice_to_cmd(a)
        if cmd:
            best = (a, cs, cmd)
            break
    if not best:
        comm("NIGHTLOOP", f"Voice: not understood \"{alts[0]}\"", None, "unable")
        return {"ok": False, "message": f"Not understood: {alts[0]}", "heard": alts[0]}, 400
    heard, cs, cmd = best
    if cs:
        known = {fl["leg"]["cs"] for fl in AI.flights}
        if cs not in known:
            comm("NIGHTLOOP", f"Voice: heard \"{heard}\" - no AI flight {cs}", None, "unable")
            return {"ok": False, "message": f"No AI flight {cs}", "heard": heard}, 400
    text = (cs + " " if cs else "") + cmd
    comm("NIGHTLOOP", f"Voice: \"{heard}\" -> {text}", None, "event")
    ok, msg = parse_and_apply(text, body.get("id"))
    AI.wake.set()
    return {"ok": ok, "message": msg, "heard": heard, "command": text}, 200 if ok else 400


# ---------------------------------------------------------------- System: Raspberry Pi controls, updates, discovery

IS_PI = os.path.exists("/proc/device-tree/model") and "raspberry" in open("/proc/device-tree/model", errors="ignore").read().lower() \
    if os.path.exists("/proc/device-tree/model") else False
NL_OS = os.path.exists("/opt/nightloop/.nightloop-os")      # running on NIGHTLOOP OS (own Raspberry Pi image)
DISCOVERY_PORT = 8089
SRV = {"srv": None, "restart": False}
CODE_FILES = ("server.py", "static")


def private_ip(ip):
    return ip.startswith(("127.", "10.", "192.168.", "169.254.", "::1", "::ffff:127.", "::ffff:10.", "::ffff:192.168.")) or \
        bool(re.match(r"^(::ffff:)?172\.(1[6-9]|2\d|3[01])\.", ip))


def lan_ips():
    import socket
    ips = set()
    try:
        s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s_.connect(("10.255.255.255", 1))
        ips.add(s_.getsockname()[0]); s_.close()
    except Exception:
        pass
    try:
        for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if not a[4][0].startswith("127."):
                ips.add(a[4][0])
    except Exception:
        pass
    return sorted(ips)


def run_cmd(args, timeout=30):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def api_system(q):
    import socket, platform
    return {"is_pi": IS_PI, "nightloop_os": NL_OS, "platform": platform.system(), "hostname": socket.gethostname(), "ips": lan_ips(),
            "port": int(CFG.get("port", 8080)), "version": VERSION, "lan": bool(CFG.get("lan", True)),
            "update_source": CFG.get("update_source", ""), "update_source_version": code_version(CFG.get("update_source", ""))}


def wifi_list(q):
    if not IS_PI:
        return {"ok": False, "error": "Wi-Fi settings are only available on the Raspberry Pi", "networks": []}
    run_cmd(["sudo", "-n", "nmcli", "dev", "wifi", "rescan"], 15)
    rc, out = run_cmd(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list"], 20)
    nets, seen = [], set()
    for line in out.splitlines():
        parts = re.split(r"(?<!\\):", line)
        if len(parts) < 4 or not parts[1] or parts[1] in seen:
            continue
        seen.add(parts[1])
        nets.append({"ssid": parts[1].replace("\\:", ":"), "signal": int(num(parts[2])), "secure": bool(parts[3].strip()), "active": parts[0] == "*"})
    nets.sort(key=lambda n: (not n["active"], -n["signal"]))
    return {"ok": rc == 0, "networks": nets, "ips": lan_ips()}


def wifi_connect(body):
    if not IS_PI:
        return {"ok": False, "message": "Only available on the Raspberry Pi"}, 400
    ssid = str(body.get("ssid", ""))[:64]
    pw = str(body.get("password", ""))[:128]
    if not ssid:
        return {"ok": False, "message": "Choose a network"}, 400
    args = ["sudo", "-n", "nmcli", "dev", "wifi", "connect", ssid] + (["password", pw] if pw else [])
    rc, out = run_cmd(args, 45)
    log(f"Wi-Fi connect {ssid}: {out.strip()[:200]}")
    return {"ok": rc == 0, "message": out.strip()[:300] or ("Connected" if rc == 0 else "Failed")}, 200 if rc == 0 else 400


def power(body):
    act = body.get("action")
    if not IS_PI:
        return {"ok": False, "message": "Shutdown and restart buttons only work on the Raspberry Pi"}, 400
    with AI.lock:
        AI.save()
    if act == "shutdown":
        threading.Timer(1.0, lambda: run_cmd(["sudo", "-n", "shutdown", "-h", "now"])).start()
        return {"ok": True, "message": "Shutting down - wait until the green light stops blinking, then unplug"}, 200
    if act == "reboot":
        threading.Timer(1.0, lambda: run_cmd(["sudo", "-n", "reboot"])).start()
        return {"ok": True, "message": "Restarting..."}, 200
    if act == "exit_kiosk":
        if NL_OS:                                   # NIGHTLOOP OS has no desktop: just reload the screen
            run_cmd(["sudo", "-n", "systemctl", "restart", "nightloop-kiosk"])
            return {"ok": True, "message": "Reloading the screen..."}, 200
        run_cmd(["pkill", "-f", "nightloop-kiosk"])
        return {"ok": True, "message": "Closed the full-screen simulator"}, 200
    return {"ok": False, "message": "Unknown action"}, 400


OS_UPD = {"running": False, "log": ""}


def os_update(body):
    if not IS_PI:
        return {"ok": False, "message": "Only on the Raspberry Pi"}, 400
    if OS_UPD["running"]:
        return {"ok": True, "message": "Already running", "log": OS_UPD["log"][-2000:]}, 200

    def job():
        OS_UPD.update(running=True, log="Updating Raspberry Pi OS...\n")
        for args in (["sudo", "-n", "apt-get", "update"], ["sudo", "-n", "apt-get", "-y", "full-upgrade"]):
            rc, out = run_cmd(args, 3600)
            OS_UPD["log"] += out[-4000:] + f"\n(exit {rc})\n"
        OS_UPD["log"] += "Done. Restart the Pi to finish.\n"
        OS_UPD["running"] = False
    threading.Thread(target=job, daemon=True).start()
    return {"ok": True, "message": "Started - this can take 10-30 minutes"}, 200


def os_update_status(q):
    return {"running": OS_UPD["running"], "log": OS_UPD["log"][-3000:]}


def code_version(folder):
    try:
        if folder and os.path.exists(os.path.join(folder, "server.py")):
            with open(os.path.join(folder, "server.py"), encoding="utf-8") as f:
                m = re.search(r'APP, VERSION = "NIGHTLOOP", "([^"]+)"', f.read(20000))
            return m.group(1) if m else None
    except Exception:
        pass
    return None


def vtuple(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", v or "0"))


def make_package():
    """Zip of the program files (no data, no settings) for another NIGHTLOOP to install."""
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(ROOT, "server.py"), "server.py")
        for base, _, files in os.walk(STATIC):
            for fn in files:
                full = os.path.join(base, fn)
                z.write(full, os.path.relpath(full, ROOT).replace("\\", "/"))
        if os.path.exists(os.path.join(ROOT, "os_update.sh")):     # optional one-time system update for NIGHTLOOP OS
            z.write(os.path.join(ROOT, "os_update.sh"), "os_update.sh")
        z.writestr("VERSION", VERSION)
    return buf.getvalue()


def install_package(blob):
    import io, zipfile
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    if "server.py" not in names or "VERSION" not in names:
        raise ValueError("not a NIGHTLOOP update package")
    for n in names:
        if n.startswith("/") or ".." in n.split("/"):
            raise ValueError("unsafe file name in package")
    newv = z.read("VERSION").decode().strip()
    bak = os.path.join(DATA, "backup_" + VERSION)
    if os.path.exists(bak):
        shutil.rmtree(bak, ignore_errors=True)
    os.makedirs(bak, exist_ok=True)
    shutil.copy2(os.path.join(ROOT, "server.py"), bak)
    shutil.copytree(STATIC, os.path.join(bak, "static"), dirs_exist_ok=True)
    for n in names:
        if n == "VERSION" or n.endswith("/"):
            continue
        dst = os.path.join(ROOT, *n.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst + ".new", "wb") as f:
            f.write(z.read(n))
        os.replace(dst + ".new", dst)
    log(f"Update installed: {VERSION} -> {newv} (backup in {bak})")
    if NL_OS and "os_update.sh" in names:
        rc, out = run_cmd(["sudo", "-n", "/opt/nightloop/system-update.sh"], 1800)
        log(f"System update script finished ({rc}): {out[-300:]}")
    return newv


def schedule_restart():
    """Restart the server process with the new files (the page reconnects by itself)."""
    def go():
        time.sleep(1.5)
        with AI.lock:
            AI.save()
        try:
            if SRV["srv"]:
                SRV["srv"].server_close()
        except Exception:
            pass
        if getattr(sys, "frozen", False):                 # Windows app (NIGHTLOOP.exe): reopen the whole app
            os.execv(sys.executable, [sys.executable] + (["--server"] if os.environ.get("NIGHTLOOP_APP") != "1" else []))
        os.execv(sys.executable, [sys.executable, os.path.join(ROOT, "server.py")] + sys.argv[1:])
    threading.Thread(target=go, daemon=True).start()


def update_pull(body):
    src = str(body.get("source", "")).rstrip("/")
    if not re.match(r"^https?://[\w.\-:\[\]]+$", src):
        return {"ok": False, "message": "Give the address of the PC, e.g. http://192.168.1.20:8080"}, 400
    try:
        info = http_json(src + "/api/system", timeout=8)
        if vtuple(info.get("version")) < vtuple(VERSION) and not body.get("force"):
            return {"ok": False, "message": f"That NIGHTLOOP is version {info.get('version')}, this one is {VERSION} - nothing newer to install"}, 400
        req = urllib.request.Request(src + "/api/update/package", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            blob = r.read()
        newv = install_package(blob)
    except Exception as e:
        return {"ok": False, "message": f"Update failed: {e}"}, 400
    schedule_restart()
    return {"ok": True, "message": f"Installed version {newv} - restarting"}, 200


def update_local(body):
    """Windows app: copy newer program files from the update folder (the NIGHTLOOP development folder)."""
    src = str(body.get("folder") or CFG.get("update_source") or "").strip()
    if body.get("folder"):
        CFG["update_source"] = src
        save_config()
    v = code_version(src)
    if not v:
        return {"ok": False, "message": f"No NIGHTLOOP files found in '{src}'"}, 400
    if os.path.abspath(src) == os.path.abspath(ROOT):
        return {"ok": False, "message": "That is the folder this NIGHTLOOP already runs from - it is always up to date"}, 400
    if vtuple(v) <= vtuple(VERSION) and not body.get("force"):
        return {"ok": False, "message": f"Already up to date (this {VERSION}, folder {v})"}, 400
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(os.path.join(src, "server.py"), "server.py")
        for base, _, files in os.walk(os.path.join(src, "static")):
            for fn in files:
                full = os.path.join(base, fn)
                z.write(full, os.path.relpath(full, src).replace("\\", "/"))
        z.writestr("VERSION", v)
    try:
        install_package(buf.getvalue())
    except Exception as e:
        return {"ok": False, "message": f"Update failed: {e}"}, 400
    schedule_restart()
    return {"ok": True, "message": f"Installed version {v} - restarting"}, 200


def update_push(body):
    """Tell another NIGHTLOOP (the Raspberry Pi) to pull this computer's version."""
    tgt = str(body.get("target", "")).rstrip("/")
    if not re.match(r"^https?://[\w.\-:\[\]]+$", tgt):
        return {"ok": False, "message": "Give the Pi address, e.g. http://nightloop.local:8080"}, 400
    my = body.get("me") or ""
    if not my:
        ips = [i for i in lan_ips() if not i.startswith("169.254.")]
        if not ips:
            return {"ok": False, "message": "This computer has no network address"}, 400
        my = f"http://{ips[0]}:{int(CFG.get('port', 8080))}"
    try:
        res = http_json(tgt + "/api/update/pull", timeout=90, data=json.dumps({"source": my, "force": bool(body.get("force"))}).encode())
        return res, 200 if res.get("ok") else 400
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode()), 400
        except Exception:
            return {"ok": False, "message": f"The Pi answered {e.code}"}, 400
    except Exception as e:
        return {"ok": False, "message": f"Could not reach {tgt}: {e}"}, 400


def discovery_listener():
    import socket
    try:
        s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s_.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s_.bind(("", DISCOVERY_PORT))
    except Exception as e:
        log(f"Discovery off ({e})")
        return
    import platform
    while True:
        try:
            data, addr = s_.recvfrom(512)
            if data.startswith(b"NIGHTLOOP?") and private_ip(addr[0]):
                ans = {"name": socket.gethostname(), "version": VERSION, "port": int(CFG.get("port", 8080)), "is_pi": IS_PI,
                       "platform": platform.system()}
                s_.sendto(b"NIGHTLOOP!" + json.dumps(ans).encode(), addr)
        except Exception:
            time.sleep(1)


def api_peers(q):
    """Find other NIGHTLOOP computers on the home network (UDP broadcast)."""
    import socket
    found, mine_ = {}, set(lan_ips())
    try:
        s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s_.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s_.settimeout(0.3)
        for _ in range(3):
            try:
                s_.sendto(b"NIGHTLOOP?", ("255.255.255.255", DISCOVERY_PORT))
            except Exception:
                pass
            end = time.time() + 0.6
            while time.time() < end:
                try:
                    data, addr = s_.recvfrom(1024)
                except socket.timeout:
                    continue
                if data.startswith(b"NIGHTLOOP!") and addr[0] not in mine_:
                    info = json.loads(data[10:].decode())
                    info["url"] = f"http://{addr[0]}:{info.get('port', 8080)}"
                    found[addr[0]] = info
    except Exception as e:
        return {"peers": [], "error": str(e)}
    return {"peers": list(found.values())}


def quit_app(body):
    def go():
        time.sleep(0.5)
        with AI.lock:
            AI.save()
        os._exit(0)
    threading.Thread(target=go, daemon=True).start()
    return {"ok": True}, 200


AI = AiFeed()
FEEDS = [AI, VatsimFeed(), IvaoFeed(), RealFeed()]
ROUTE_CACHE = {}


# ---------------------------------------------------------------- API

def parse_box(q):
    s, w, n, e = (num(q.get(k), d) for k, d in (("s", -90), ("w", -180), ("n", 90), ("e", 180)))
    return s, w, n, e, num(q.get("z"), 5)


def api_traffic(q):
    """Aircraft inside any of the requested view boxes. b = 's,w,n,e,z|s,w,n,e,z|...' (one per window)."""
    boxes = []
    for part in (q.get("b") or "").split("|"):
        v = [num(x, None) for x in part.split(",")]
        if len(v) == 5 and None not in v:
            boxes.append(tuple(v))
    if not boxes:
        boxes = [parse_box(q)]
    centers = []
    for s_, w, n, e, z in boxes:
        clat = (s_ + n) / 2
        clon = (w + e) / 2 if w <= e else ((w + e + 360) / 2 + 180) % 360 - 180
        centers.append((clat, clon))
    VIEW.update(t=time.time(), lat=centers[0][0], lon=centers[0][1], z=min(b[4] for b in boxes), centers=centers)
    now, out = time.time(), []
    for f in FEEDS:
        if not CFG["sources"].get(f.name, True):
            continue
        for t in f.targets:
            la, lo = t["lat"], t["lon"]
            if any(s_ <= la <= n and in_lon(lo, w, e) for s_, w, n, e, _ in boxes):
                d = dict(t)
                d["age"] = round(max(0.0, now - t["ts"]), 1)
                out.append(d)
    return {"targets": out[:15000], "feeds": [f.status() for f in FEEDS], "home": CFG["home"],
            "ai": {"count": CFG.get("ai_flights", 300), "paused": bool(AI.paused_at), "paused_at": AI.paused_at,
                   "home_share": CFG.get("ai_home_share", 0.1)},
            "server_time": now, "version": VERSION}


def api_airports(q):
    if not AP_STATE["ready"]:
        return {"loading": True, "error": AP_STATE["error"], "airports": [], "runways": []}
    s, w, n, e, z = parse_box(q)
    allowed = AP_TYPES[:1] if z < 5 else AP_TYPES[:2] if z < 7.5 else AP_TYPES
    out = []
    for a in AIRPORTS:
        if a["type"] in allowed and s <= a["lat"] <= n and in_lon(a["lon"], w, e):
            out.append(a)
            if len(out) >= 3000:
                break
    rws = []
    if z >= 9:
        # an airport's runways stay visible even when its reference point is outside the window (zoomed in close)
        m_lat = 0.12
        m_lon = 0.12 / max(math.cos(math.radians((s + n) / 2)), 0.2)
        seen = set()
        for a in AIRPORTS:
            if (s - m_lat) <= a["lat"] <= (n + m_lat) and in_lon(a["lon"], w - m_lon, e + m_lon) and a["ident"] not in seen:
                seen.add(a["ident"])
                rws.extend(RUNWAYS.get(a["ident"], []))
                if len(rws) > 6000:
                    break
    return {"airports": out, "runways": rws}


def find_target(id_):
    for f in FEEDS:
        for t in f.targets:
            if t["id"] == id_:
                return t
    return None


def api_route(q):
    t = find_target(q.get("id", ""))
    if not t:
        return {"dep": None, "arr": None, "via": None, "error": "aircraft not found"}
    dep, arr = airport_brief(t["dep"]), airport_brief(t["arr"])
    if dep or arr:
        return {"dep": dep, "arr": arr, "via": "AI schedule" if t["source"] == "ai" else "filed flight plan"}
    if t["source"] != "real":
        return {"dep": None, "arr": None, "via": None, "error": "no flight plan filed"}
    cs = t["callsign"].strip().upper()
    c = ROUTE_CACHE.get(cs)
    if c and time.time() - c[0] < 1800:
        return c[1]
    res = {"dep": None, "arr": None, "via": None}
    try:   # 1st choice: adsbdb.com callsign -> route database
        d = http_json("https://api.adsbdb.com/v0/callsign/" + urllib.parse.quote(cs), timeout=10)
        fr = ((d or {}).get("response") or {}).get("flightroute") or {}
        o, de = fr.get("origin") or {}, fr.get("destination") or {}
        if o and de:
            res = {"dep": airport_brief(o.get("icao_code") or o.get("iata_code"), {"lat": o.get("latitude"), "lon": o.get("longitude"), "name": o.get("name")}),
                   "arr": airport_brief(de.get("icao_code") or de.get("iata_code"), {"lat": de.get("latitude"), "lon": de.get("longitude"), "name": de.get("name")}),
                   "via": "adsbdb.com route database (callsign-based, may be wrong)"}
            if res["dep"] and res["arr"]:
                ROUTE_CACHE[cs] = (time.time(), res)
                return res
    except Exception:
        pass
    res = {"dep": None, "arr": None, "via": None}
    try:   # 2nd choice: adsb.lol routeset
        body = json.dumps({"planes": [{"callsign": cs, "lat": t["lat"], "lng": t["lon"]}]}).encode()
        d = http_json("https://api.adsb.lol/api/0/routeset", data=body, timeout=10)
        item = d[0] if isinstance(d, list) and d else d if isinstance(d, dict) else {}
        aps = item.get("_airports") or []
        if len(aps) >= 2:
            first, last = aps[0], aps[-1]
            res = {"dep": airport_brief(first.get("icao") or first.get("iata"), first),
                   "arr": airport_brief(last.get("icao") or last.get("iata"), last)}
        elif item.get("airport_codes") and "-" in item["airport_codes"]:
            codes = item["airport_codes"].split("-")
            res = {"dep": airport_brief(codes[0]), "arr": airport_brief(codes[-1])}
        res["via"] = "adsb.lol route database (callsign-based, may be wrong)" if res.get("dep") else None
        if not res.get("dep"):
            res["error"] = "route not known for this callsign"
    except Exception as e:
        res["error"] = "route lookup failed: " + str(e)[:80]
    ROUTE_CACHE[cs] = (time.time(), res)
    return res


def api_search(q):
    term = str(q.get("q", "")).strip().upper()
    kind = q.get("kind", "all")
    if len(term) < 2:
        return {"aircraft": [], "airports": []}
    ac = []
    if kind in ("all", "flights"):
        exact, part = [], []
        for f in FEEDS:
            if not CFG["sources"].get(f.name, True):
                continue
            for t in f.targets:
                cs = t["callsign"].upper()
                hit = cs.startswith(term) or term in cs or (t.get("reg") and term in str(t["reg"]).upper()) \
                    or (len(term) >= 3 and (t["dep"] == term or t["arr"] == term)) or (t["type"] and t["type"].upper() == term)
                if hit:
                    row = {k: t.get(k) for k in ("id", "source", "callsign", "type", "lat", "lon", "dep", "arr", "alt", "gs", "ground")}
                    (exact if cs.startswith(term) else part).append(row)
        ac = (exact + part)[:20 if kind == "flights" else 8]
    aps, seen = [], set()
    if kind in ("all", "airports"):
        exact = AP_INDEX.get(term)
        if exact:
            aps.append(exact); seen.add(exact["ident"])
        if len(term) >= 3:
            for a in AIRPORTS:
                if a["ident"] in seen:
                    continue
                if a["code"].startswith(term) or term in a["name"].upper() or (a["city"] and term in a["city"].upper()):
                    aps.append(a); seen.add(a["ident"])
                    if len(aps) >= (15 if kind == "airports" else 6):
                        break
    return {"aircraft": ac, "airports": [{k: a[k] for k in ("code", "name", "city", "lat", "lon", "type")} for a in aps]}


def public_config():
    return {"home": CFG["home"], "sources": CFG["sources"], "real_providers": CFG["real_providers"],
            "ivao_key_set": bool(CFG.get("ivao_api_key", "").strip()), "airports_ready": AP_STATE["ready"],
            "ai_flights": CFG.get("ai_flights", 300), "version": VERSION,
            "freeflight_installed": bool(freeflight_exe())}


def set_home(body):
    code = str(body.get("icao", "")).strip().upper()
    if not AP_STATE["ready"]:
        return {"error": "Airport data is still loading - try again in a minute"}, 503
    a = AP_INDEX.get(code)
    if not a:
        return {"error": f"Airport {code} not found"}, 404
    CFG["home"] = {"icao": a["code"], "lat": a["lat"], "lon": a["lon"], "name": a["name"]}
    save_config()
    log(f"Home airport set to {a['code']} {a['name']}")
    return {"home": CFG["home"]}, 200


def set_source(body):
    name = body.get("name")
    if name not in CFG["sources"]:
        return {"error": "unknown source"}, 400
    CFG["sources"][name] = bool(body.get("enabled"))
    save_config()
    log(f"Source {name} {'on' if CFG['sources'][name] else 'off'}")
    return public_config(), 200


def set_provider(body):
    name = body.get("name")
    if name not in RealFeed.PROVIDERS:
        return {"error": "unknown provider"}, 400
    CFG["real_providers"][name] = bool(body.get("enabled"))
    save_config()
    msg = ""
    if name == "freeflight" and CFG["real_providers"][name]:
        msg = start_freeflight()
    log(f"Real-world provider {name} {'on' if CFG['real_providers'][name] else 'off'} {msg}")
    return dict(public_config(), message=msg), 200


def set_ai(body):
    if "paused" in body:
        AI.set_paused(bool(body["paused"]))
    if "home_share" in body:
        CFG["ai_home_share"] = max(0.0, min(1.0, num(body["home_share"], 0.1)))
        save_config()
    if "count" in body:
        n = int(max(0, min(5000, num(body.get("count"), 300))))
        AI.set_count(n)
        log(f"AI flights set to {n}")
    AI.wake.set()
    return {"count": CFG.get("ai_flights"), "paused": bool(AI.paused_at), "home_share": CFG.get("ai_home_share")}, 200


def refresh_now():
    for f in FEEDS:
        f.wake.set()
    log("Refresh requested")
    return {"ok": True}, 200


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=STATIC, **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def lan_ok(self):
        ip = self.client_address[0]
        if private_ip(ip):
            return True
        self.send_json({"error": "NIGHTLOOP only answers devices on your home network"}, 403)
        return False

    def local_only(self):
        ip = self.client_address[0]
        return ip.startswith(("127.", "::1", "::ffff:127."))

    def do_GET(self):
        if not self.lan_ok():
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/tts":
            b = tts_wav(q)
            if b is None:
                return self.send_json({"error": "natural voices not installed"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if u.path == "/api/update/package":
            b = make_package()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        routes = {"/api/traffic": api_traffic, "/api/airports": api_airports, "/api/route": api_route, "/api/search": api_search,
                  "/api/comms": api_comms, "/api/runways": api_runways, "/api/tower": api_tower,
                  "/api/center": api_center, "/api/navaids": api_navaids, "/api/ground": api_ground, "/api/config": lambda q: public_config(),
                  "/api/ground/diagram": api_ground_diagram, "/api/system": api_system, "/api/system/wifi": wifi_list,
                  "/api/system/osupdate": os_update_status, "/api/peers": api_peers,
                  "/api/positions": lambda q: {"positions": CFG.get("positions")},
                  "/api/voice/local": voice_local_info, "/api/voice/local/partial": voice_local_partial,
                  "/api/tts/info": tts_info, "/api/data/status": data_status,
                  "/api/update/github": lambda q: {"repo": github_repo(), "auto": CFG.get("auto_update", True), **GH_UPD}}
        if u.path in routes:
            return self.send_json(routes[u.path](q))
        return super().do_GET()

    def do_POST(self):
        if not self.lan_ok():
            return
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send_json({"error": "bad json"}, 400)
        routes = {"/api/home": set_home, "/api/sources": set_source, "/api/providers": set_provider,
                  "/api/ai": set_ai, "/api/refresh": lambda b: refresh_now(),
                  "/api/command": api_command, "/api/shadow": shadow, "/api/tower": set_tower, "/api/center": set_center,
                  "/api/ground": set_ground, "/api/voice": api_voice, "/api/positions": set_positions,
                  "/api/system/wifi": wifi_connect, "/api/system/power": power, "/api/system/osupdate": os_update,
                  "/api/update/pull": update_pull, "/api/ground/preview": api_ground_preview,
                  "/api/voice/local/install": voice_local_install, "/api/voice/local/start": voice_local_start,
                  "/api/voice/local/stop": voice_local_stop, "/api/voice/mic": set_mic,
                  "/api/tts/install": tts_install, "/api/data/refresh": data_refresh, "/api/update/github": github_check, "/api/update/local": update_local, "/api/update/push": update_push}
        if u.path == "/api/quit":
            return self.send_json(*quit_app(body)) if self.local_only() else self.send_json({"error": "local only"}, 403)
        if u.path in routes:
            return self.send_json(*routes[u.path](body))
        return self.send_json({"error": "not found"}, 404)


def main():
    save_config()
    threading.Thread(target=airport_loader, daemon=True).start()
    for f in FEEDS:
        threading.Thread(target=f.run, daemon=True).start()
    if CFG["real_providers"].get("freeflight") and CFG.get("freeflight_autostart", True):
        log("FreeFlight: " + start_freeflight())
    port = int(CFG.get("port", 8080))
    bind = "0.0.0.0" if (CFG.get("lan", True) or IS_PI) else "127.0.0.1"
    ThreadingHTTPServer.allow_reuse_address = True
    for attempt in range(20):
        try:
            srv = ThreadingHTTPServer((bind, port), Handler)
            break
        except OSError:
            if attempt == 19:
                raise
            time.sleep(0.5)                       # an older copy is still closing (after an update)
    SRV["srv"] = srv
    threading.Thread(target=discovery_listener, daemon=True).start()
    threading.Thread(target=github_auto, daemon=True).start()
    url = f"http://localhost:{port}/"
    log(f"{APP} v{VERSION} running at {url}" + (f" - other devices: " + ", ".join(f"http://{i}:{port}" for i in lan_ips()) if bind == "0.0.0.0" else ""))
    log("Keep this window open. Close it to stop the simulator.")
    if os.environ.get("NIGHTLOOP_NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    finally:
        with AI.lock:
            AI.save()


if __name__ == "__main__":
    main()
