#!/opt/palapi/bin/python
"""EZPAL API - Parses .sav + live mod JSON, serves pal data + suggestions."""
import os, sys, json, threading
from datetime import datetime, timezone
from flask import Flask, jsonify, request
from flask_cors import CORS
from collections import defaultdict

app = Flask(__name__)
CORS(app)

# --- Config ---
SAVE_DIR = os.environ.get("PALWORLD_SAVE_DIR") or "/var/lib/pelican/volumes/0ef8fccb-b40a-4843-b24d-35fdc0e52ba1/Pal/Saved/SaveGames"
LIVE_DATA_DIR = os.environ.get("EZPAL_DATA_DIR") or "/var/lib/pelican/volumes/0ef8fccb-b40a-4843-b24d-35fdc0e52ba1/ezpal_live/"
_CACHE_PATH = os.path.join(os.path.dirname(__file__), "paldata.json")
_CACHE_REFRESH_INTERVAL = 60
_cache = {"last_updated": None, "players": {}, "refreshing": False}
_cache_lock = threading.Lock()

# -- Optional palworld_save_tools --
HAS_PAL_SAVE = False
PALWORLD_TYPE_OVERRIDES = {}
PALWORLD_SAVE_TYPE_OVERRIDES = {}
try:
    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.palsav import decompress_sav_to_gvas
    from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES
    PALWORLD_TYPE_OVERRIDES = PALWORLD_TYPE_HINTS
    PALWORLD_SAVE_TYPE_OVERRIDES = PALWORLD_CUSTOM_PROPERTIES
    HAS_PAL_SAVE = True
    print("palworld_save_tools loaded", file=sys.stderr)
except ImportError:
    print("palworld_save_tools not available — .sav parsing disabled", file=sys.stderr)
# --- End Config ---

PASSIVE_SCORES = {
    "Legend": 100, "Demon God": 95, "Diamond Body": 70, "Vampiric": 70,
    "Remarkable Craftsmanship": 120, "Swift": 70, "Lucky": 85,
    "Eternal Flame": 90, "Siren of the Void": 90, "Invader": 90,
    "Savior": 90, "Lunker": 90, "Eternal Engine": 90, "King of the Waves": 90,
    "Flame Emperor": 90, "Ice Emperor": 90, "Lord of the Sea": 90,
    "Lord of Lightning": 90, "Divine Dragon": 90, "Lord of the Underworld": 90,
    "Earth Emperor": 90, "Celestial Emperor": 90, "Spirit Emperor": 90,
    "Ferocious": 80, "Burly Body": 75, "Artisan": 80, "Serenity": 70,
    "Vanguard": 65, "Stronghold Strategist": 60, "Philanthropist": 55,
    "Infinite Stamina": 70, "Ace Swimmer": 65, "Workaholic": 50,
    "Musclehead": 80, "Serious": 60, "Runner": 55,
    "Work Slave": 50, "Conceited": 35, "Nimble": 30, "Hooligan": 35,
    "Brave": 25, "Nocturnal": 30, "Impatient": 25, "Hard Skin": 25,
    "Pyromaniac": 25, "Coldblooded": 25, "Hydromaniac": 25, "Capacitor": 25,
    "Blood of the Dragon": 25, "Veil of Darkness": 25, "Power of Gaia": 25,
    "Fragrant Foliage": 25, "Zen Mind": 25,
}
NEGATIVE_PASSIVES = {
    "Pacifist", "Brittle", "Slacker", "Bottomless Stomach", "Destructive",
    "Clumsy", "Coward", "Downtrodden", "Glutton", "Mercy Hit", "Unstable",
    "Sickly", "Shabby", "Easygoing",
}
PAL_NAMES = {
    "Anubis": "Anubis", "Jormuntide": "Jormuntide", "Jormuntide Ignis": "Jormuntide Ignis",
    "Lyleen": "Lyleen", "Lyleen Noct": "Lyleen Noct", "Frostallion": "Frostallion",
    "Frostallion Noct": "Frostallion Noct", "Jetragon": "Jetragon", "Paladius": "Paladius",
    "Necromus": "Necromus", "Shadowbeak": "Shadowbeak", "Astegon": "Astegon",
    "Grizzbolt": "Grizzbolt", "Menasting": "Menasting", "Blazamut": "Blazamut",
    "Suzaku": "Suzaku", "Suzaku Aqua": "Suzaku Aqua",
}
TIER_NAMES = {0: "None", 1: "1-Star", 2: "2-Star", 3: "3-Star", 4: "4-Star"}

# Known Palworld pal internal character IDs (for PlM byte-scan fallback)
_PAL_IDS = [
    "Anubis", "Jormuntide", "Lyleen", "Frostallion", "Jetragon",
    "Shadowbeak", "Astegon", "Grizzbolt", "Menasting", "Blazamut",
    "Suzaku", "Paladius", "Necromus", "Lovander", "Wixen",
    "Foxcicle", "Reindrix", "Kitsun", "Mossanda", "Kingpaca",
    "LilyQueen", "Warsect", "Broncherry", "Beakon", "Helzephyr",
    "Ragnahawk", "Univolt", "Surfent", "Dinossom", "Cryolinx",
    "Vaelet", "Hangyu", "Katress", "Relaxaurus", "Chillet",
    "Grintale", "Sweepa", "Cinnamoth", "Petallia", "Robinquill",
    "Killamari", "Mammorest", "Dumud", "Verdash", "Elizabee",
    "Gorirat", "Tombat", "Loupmoon", "Galeclaw", "Melpaca",
    "Eikthyrdeer", "Nitewing", "Rayhound", "Lamball", "Cattiva",
    "Chikipi", "Lifmunk", "Foxparks", "Fuack", "Sparkit",
    "Tanzee", "Rooby", "Pengullet", "Penking", "Jolthog",
    "Gumoss", "Vixy", "Hoocrates", "Teafant", "Depresso",
    "Cremis", "Daedream", "Rushoar", "Nox", "Fuddler",
    "Mau", "Celaray", "Direhowl", "Felbat", "Quivern",
    "Blazehowl", "Caprity", "Flambelle", "Arsox", "Cawgnito",
    "Leezpunk", "Woolipop", "Bristla", "Gobfin", "Dazzi",
    "Sibelyx", "Tocotoco",
]


# ═══════════════════════════════════════════════
#  SAV PARSING
# ═══════════════════════════════════════════════

def find_world_dirs():
    if not os.path.isdir(SAVE_DIR):
        return []
    dirs = []
    for root, dirs_list, files in os.walk(SAVE_DIR):
        parts = root.replace("\\", "/").split("/")
        if "backup" in parts:
            continue
        if "Level.sav" in files and os.path.isdir(os.path.join(root, "Players")):
            dirs.append(root)
    return dirs


def read_sav_gvas(raw):
    magic = raw[8:11]
    if magic == b"PlM":
        return raw[20:], 0
    elif magic == b"PlZ":
        return decompress_sav_to_gvas(raw)
    else:
        raise Exception(f"Unknown save magic: {magic!r}")


def scan_plm_pals(gvas_bytes):
    found = set()
    for pal_id in _PAL_IDS:
        if pal_id.encode() in gvas_bytes:
            found.add(pal_id)
    return sorted(found)


def _parse_pal_raw(raw, source="unknown"):
    """Extract every available field from a pal's RawData dict."""
    try:
        if not isinstance(raw, dict):
            return None
        cid = raw.get("character_id", "Unknown")
        pal = {
            "_source": source,
            # Identity
            "character_id":  cid,
            "display_name":  PAL_NAMES.get(cid, cid.replace("_", " ").title()),
            "name":          cid,
            "nickname":      raw.get("nickname") or "",
            "gender":        raw.get("gender", ""),
            # Stats
            "level":         int(raw.get("level", 1)),
            "rank":          int(raw.get("rank", 0)),
            "rank_up_count": int(raw.get("rank_up_count", 0)),
            # Talents / IVs
            "talent_hp":     int(raw.get("talent_hp", raw.get("talent_hp", 0))),
            "talent_attack": int(raw.get("talent_attack", raw.get("talent_attack", 0))),
            "talent_defense": int(raw.get("talent_defense", raw.get("talent_defense", 0))),
            # Soul stats
            "soul_hp":       int(raw.get("soul_hp", 0)),
            "soul_attack":   int(raw.get("soul_attack", 0)),
            "soul_defense":  int(raw.get("soul_defense", 0)),
            # Flags
            "is_boss":       bool(raw.get("is_boss", False)),
            "is_alpha":      bool(raw.get("is_boss", False)),
            "is_tower":      bool(raw.get("is_tower", False)),
            "is_rare":       bool(raw.get("is_rare", False)),
            "is_lucky":      bool(raw.get("is_lucky", False)),
            "is_paldeck":    bool(raw.get("is_paldeck", False)),
            # Battle stats
            "hp":            int(raw.get("hp", 0)),
            "max_hp":        int(raw.get("max_hp", 0)),
            "attack":        float(raw.get("attack", 0)),
            "defense":       float(raw.get("defense", 0)),
            "craft_speed":   float(raw.get("craft_speed", 1.0)),
            "move_speed":    float(raw.get("move_speed", 1.0)),
            "support":       float(raw.get("support", 0)),
            # Food / sanity
            "food_amount":   int(raw.get("food_amount", 0)),
            "food_remaining": float(raw.get("food_remaining", 0)),
            "sanity":        float(raw.get("sanity", 1.0)),
            # Skills
            "passive_skills": [],
            "active_skills":  [],
            "learned_skills": [],
            "partner_skill":  raw.get("partner_skill", ""),
            # Ownership
            "player_uid":    "",
            "slot":          int(raw.get("slot", 0)),
            "container_id":  {},
            # Breeding
            "breeding_count": int(raw.get("breeding_count", 0)),
            "prefer_work":    int(raw.get("prefer_work", 0)),
            "stored_item":    {},
            "pal_gear":       {},
            "elements":       [],
            "work_suitability": [],
            "taming":         int(raw.get("taming", 0)),
        }

        # ── Passives ──────────────────────────────────────
        if "passive_skills" in raw:
            for p in raw["passive_skills"]:
                if isinstance(p, str):
                    pal["passive_skills"].append(p.replace("_", " ").title())
        # Handle both naming conventions
        if "passive_skill_list" in raw:
            for p in raw.get("passive_skill_list", []):
                if isinstance(p, dict):
                    pal["passive_skills"].append(str(p.get("passive_id", p.get("id", ""))))
                elif isinstance(p, str):
                    pal["passive_skills"].append(p.replace("_", " ").title())

        # ── Active skills ─────────────────────────────────
        if "active_skills" in raw:
            for s in raw["active_skills"]:
                if isinstance(s, dict):
                    sid = s.get("skill_id", s.get("id", ""))
                    pal["active_skills"].append(sid)
                elif isinstance(s, str):
                    pal["active_skills"].append(s)

        # ── Learned skills ────────────────────────────────
        if "learned_skills" in raw:
            for s in raw["learned_skills"]:
                if isinstance(s, dict):
                    sid = s.get("skill_id", s.get("id", ""))
                    pal["learned_skills"].append(sid)
                elif isinstance(s, str):
                    pal["learned_skills"].append(s)

        # ── Elements ──────────────────────────────────────
        if "elements" in raw:
            for e in raw["elements"]:
                if isinstance(e, dict):
                    pal["elements"].append(e.get("element_type", e.get("type", "")))
                elif isinstance(e, str):
                    pal["elements"].append(e)

        # ── Owner ─────────────────────────────────────────
        uid = raw.get("player_uid")
        if isinstance(uid, bytes):
            pal["player_uid"] = uid.hex()
        elif uid:
            pal["player_uid"] = str(uid)

        # ── Scores ────────────────────────────────────────
        pal["passive_score"] = score_passives(pal["passive_skills"])
        pal["overall_score"] = calc_overall_score(pal)
        return pal
    except Exception as e:
        return None


_MAX_TRAVERSE_DEPTH = 50
_seen_pal_keys = set()

def _pal_dedup_key(raw):
    """Generate a stable dedup key from a pal's RawData dict."""
    cid = raw.get("character_id", "")
    slot = raw.get("slot", 0)
    uid = raw.get("player_uid", b"")
    if isinstance(uid, bytes):
        uid = uid.hex()[:16]
    lv = raw.get("level", 0)
    rk = raw.get("rank", 0)
    return f"{cid}|{slot}|{uid}|L{lv}|R{rk}"


def _extract_all_containers(props, data):
    """Search every property that could hold pal containers, covering all known
    Palworld save-version naming conventions."""
    container_keys = [
        "container", "pal_container", "PalContainer", "CharacterContainer",
        "OtomoCharacterContainer", "PalStorage", "PalBox",
        "IndividualCharacterContainer", "PalCharacterContainer",
        "WorkerContainer", "BaseCampContainer",
        "individual_character_handle_ids", "character_handle_ids",
    ]
    for key in container_keys:
        if key in props:
            val = props[key].value
            _traverse_for_pals(val, data, key, 1)

    # Also walk every top-level property — some patches use unique names
    for pkey, pval in props.items():
        if pkey not in container_keys and pkey not in ("SaveData", "group_0", "group_1"):
            _traverse_for_pals(pval.value, data, f"prop:{pkey}", 1)


def _traverse_for_pals(obj, data, source="unknown", depth=0):
    """Recursively search for RawData dicts. Depth-limited, deduped, source-tracked."""
    if depth > _MAX_TRAVERSE_DEPTH:
        return
    if isinstance(obj, dict):
        if "RawData" in obj:
            raw = obj["RawData"]
            if isinstance(raw, dict):
                dk = _pal_dedup_key(raw)
                if dk not in _seen_pal_keys:
                    _seen_pal_keys.add(dk)
                    pal = _parse_pal_raw(raw, source)
                    if pal:
                        data["pals"].append(pal)
        for v in obj.values():
            _traverse_for_pals(v, data, source, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _traverse_for_pals(item, data, source, depth + 1)


def _extract_save_data_pals(sd, data):
    """Extract pals from the SaveData property map.
    SaveData can have multiple deep structures; we brute-force traverse everything."""
    _traverse_for_pals(sd, data, "SaveData", 1)


def _load_player_nickname(steam_id):
    nick_path = os.path.join(os.path.dirname(__file__), "players.json")
    try:
        with open(nick_path) as f:
            nicknames = json.load(f)
        return nicknames.get(steam_id, "")
    except Exception:
        return ""


def parse_player_sav(path):
    filename = os.path.basename(path)
    default_steam_id = filename.replace(".sav", "")
    global _seen_pal_keys
    _seen_pal_keys = set()
    try:
        with open(path, "rb") as f:
            raw = f.read()
        gvas_bytes, _ = read_sav_gvas(raw)
        gvas_file = GvasFile.read(gvas_bytes, PALWORLD_TYPE_OVERRIDES, PALWORLD_SAVE_TYPE_OVERRIDES)
        data = {"steam_id": default_steam_id, "name": "", "nickname": "",
                "pals": [], "party": [], "container_stats": {}}
        props = gvas_file.properties

        # ── Player identity ──
        if "group_0" in props:
            g0 = props["group_0"].value
            if "group_1" in props:
                g1 = props["group_1"].value
                if "player_uid" in g1:
                    uid = g1["player_uid"].value
                    if isinstance(uid, bytes):
                        data["steam_id"] = uid.hex()
                    else:
                        data["steam_id"] = str(uid)
                if "player_name" in g1:
                    data["name"] = str(g1["player_name"].value)

        if not data.get("name"):
            data["name"] = _load_player_nickname(data["steam_id"])

        # ── Phase 1: SaveData ──
        if "SaveData" in props:
            sd = props["SaveData"].value
            _extract_save_data_pals(sd, data)

        # ── Phase 2: Known container properties ──
        _extract_all_containers(props, data)

        # ── Phase 3: group_0 deep search ──
        if "group_0" in props:
            g0 = props["group_0"].value
            if isinstance(g0, dict):
                for v in g0.values():
                    _traverse_for_pals(v, data, "group_0", 1)

        # ── Compute container stats ──
        container_counts = {}
        for pal in data["pals"]:
            src = pal.get("_source", "unknown")
            container_counts[src] = container_counts.get(src, 0) + 1
        data["container_stats"] = container_counts
        # Separate party vs box
        data["party"] = [p for p in data["pals"]
                         if p.get("_source", "").startswith("OtomoCharacterContainer")
                         or p.get("slot", 99) < 6]
        data["pals"].sort(key=lambda p: (p.get("_source", ""), p.get("slot", 0)))
        data["pal_count"] = len(data["pals"])
        data["party_count"] = len(data["party"])
        data["box_count"] = max(0, data["pal_count"] - data["party_count"])
        return data

    except Exception as e:
        nickname = _load_player_nickname(default_steam_id)
        pals = scan_plm_pals(open(path, "rb").read())
        return {
            "steam_id": default_steam_id,
            "name": nickname or default_steam_id,
            "nickname": nickname,
            "pals": [{"character_id": pid, "display_name": PAL_NAMES.get(pid, pid),
                       "name": pid, "level": 1, "rank": 0,
                       "passive_skills": [], "passive_score": 0, "overall_score": 2,
                       "_source": "plm_scan"}
                      for pid in pals],
            "party": [],
            "container_stats": {"plm_scan": len(pals)},
            "pal_count": len(pals),
            "party_count": 0,
            "box_count": len(pals),
            "sav_parse_error": str(e),
        }


# ═══════════════════════════════════════════════
#  ENRICHMENT
# ═══════════════════════════════════════════════

def score_passives(positives, negatives=None):
    score = 0
    for p in (positives or []):
        score += PASSIVE_SCORES.get(p, 0)
    for p in (negatives or []):
        score -= 30
    return score


def calc_overall_score(pal):
    score = pal.get("passive_score", 0)
    score += pal.get("level", 1) * 2
    score += pal.get("rank", 0) * 20
    hp   = pal.get("talent_hp", pal.get("hp_iv", 0))
    atk  = pal.get("talent_attack", pal.get("atk_iv", 0))
    dfn  = pal.get("talent_defense", pal.get("def_iv", 0))
    score += (hp + atk + dfn) / 3
    if pal.get("is_boss") or pal.get("is_alpha"):
        score += 15
    return round(score, 1)


def enrich_pal(pal):
    species = pal.get("species") or pal.get("character_id") or "Unknown"
    pal["name"]         = species
    pal["display_name"] = PAL_NAMES.get(species, species.replace("_", " ").title())
    if "_source" not in pal:
        # Mod data or .sav data without explicit source: mark from passives/neg_passives presence
        if "neg_passives" in pal or "passives" in pal:
            pal["_source"] = "live_mod"
        else:
            pal["_source"] = "enriched"

    # Normalise passives
    positives = pal.get("passives") or pal.get("passive_skills") or []
    negatives = pal.get("neg_passives") or []
    if not negatives and positives:
        negatives = [p for p in positives if p in NEGATIVE_PASSIVES]
        positives = [p for p in positives if p not in NEGATIVE_PASSIVES]
    # Normalise .sav flat passive_skills into separate lists
    if positives and not negatives:
        negatives = [p for p in positives if p in NEGATIVE_PASSIVES]
        positives = [p for p in positives if p not in NEGATIVE_PASSIVES]

    pal["passives"]     = positives
    pal["neg_passives"] = negatives
    pal["passive_skills"] = positives + negatives

    # Normalise IVs
    pal["talent_hp"]      = pal.get("talent_hp", pal.get("hp_iv", 0))
    pal["talent_attack"]  = pal.get("talent_attack", pal.get("atk_iv", 0))
    pal["talent_defense"] = pal.get("talent_defense", pal.get("def_iv", 0))

    # Aliases for dashboard
    pal["hp_iv"]  = pal["talent_hp"]
    pal["atk_iv"] = pal["talent_attack"]
    pal["def_iv"] = pal["talent_defense"]
    pal["is_alpha"] = pal.get("is_alpha", pal.get("is_boss", False))
    pal["is_boss"]  = pal.get("is_boss", pal.get("is_alpha", False))

    pal["passive_score"] = score_passives(positives, negatives)
    pal["overall_score"] = calc_overall_score(pal)
    return pal


def enrich_player(player):
    if not player.get("steam_id"):
        player["steam_id"] = player.get("player_id", "unknown")
    if not player.get("name"):
        player["name"] = player.get("player_name") or player.get("nickname") or player["steam_id"]
    if "pals" in player:
        player["pals"] = [enrich_pal(p) for p in player.get("pals", []) if p]
    # Compute box/party breakdown
    pals = player.get("pals", [])
    player["pal_count"] = len(pals)
    player["party"] = [p for p in pals
                       if p.get("_source", "") in ("live_mod", "OtomoCharacterContainer")
                       or p.get("_source", "").startswith("OtomoCharacterContainer")
                       or p.get("slot", 99) < 6]
    player["party_count"] = len(player["party"])
    player["box_count"] = max(0, player["pal_count"] - player["party_count"])
    # Container stats from _source
    cstats = {}
    for p in pals:
        src = p.get("_source", "unknown")
        cstats[src] = cstats.get(src, 0) + 1
    player["container_stats"] = cstats
    return player


# ═══════════════════════════════════════════════
#  MERGE: .sav base + mod live overlay
# ═══════════════════════════════════════════════

def _overlay_mod_pals(sav_player, mod_player):
    """Overlay mod's party-pal passives onto the .sav player's pal list.
    Mod data has better passive resolution for live party pals."""
    mod_pals = {_pal_key(p): p for p in mod_player.get("pals", [])}
    for pal in sav_player.get("pals", []):
        key = _pal_key(pal)
        mp = mod_pals.get(key)
        if mp and mp.get("passives"):
            # Mod has better passive data — overlay
            pal["passives"]     = mp.get("passives", [])
            pal["neg_passives"] = mp.get("neg_passives", [])
            pal["passive_skills"] = pal["passives"] + pal["neg_passives"]
            pal["passive_score"] = score_passives(pal["passives"], pal["neg_passives"])
            pal["overall_score"] = calc_overall_score(pal)
    return sav_player


def _pal_key(pal):
    """Unique key for matching pals between .sav and mod data."""
    cid = pal.get("character_id") or pal.get("species") or ""
    lv  = pal.get("level", 0)
    rk  = pal.get("rank", 0)
    return f"{cid}|L{lv}|R{rk}"


# ═══════════════════════════════════════════════
#  MOD JSON READING
# ═══════════════════════════════════════════════

def _read_mod_json():
    """Read and enrich the mod's all_players.json. Returns {sid: player}."""
    combined_path = os.path.join(LIVE_DATA_DIR, "all_players.json")
    if not os.path.isfile(combined_path):
        return {}
    with open(combined_path, "r") as f:
        raw = json.load(f)
    players_list = raw.get("players", raw) if isinstance(raw, dict) else raw
    if isinstance(players_list, dict):
        players_list = list(players_list.values())
    result = {}
    for player in players_list:
        sid = player.get("steam_id") or player.get("player_id") or ""
        if not sid:
            continue
        result[sid] = enrich_player(player)
    return result


# ═══════════════════════════════════════════════
#  SAV READING
# ═══════════════════════════════════════════════

def _read_sav_players():
    """Parse all .sav files and return {sid: player}."""
    if not HAS_PAL_SAVE:
        return {}
    result = {}
    world_dirs = find_world_dirs()
    for wd in world_dirs:
        player_dir = os.path.join(wd, "Players")
        if not os.path.isdir(player_dir):
            continue
        for f in os.listdir(player_dir):
            if not f.endswith(".sav"):
                continue
            path = os.path.join(player_dir, f)
            try:
                data = parse_player_sav(path)
                sid = data.get("steam_id", f.replace(".sav", ""))
                result[sid] = enrich_player(data)
            except Exception as e:
                print(f"Failed to parse {f}: {e}", file=sys.stderr)
    return result


# ═══════════════════════════════════════════════
#  SUGGESTION ENGINE
# ═══════════════════════════════════════════════

def generate_suggestions(pals):
    by_species = defaultdict(list)
    for pal in pals:
        by_species[pal["display_name"]].append(pal)

    suggestions = []
    species_summary = []

    for species, group in by_species.items():
        group.sort(key=lambda x: x["overall_score"], reverse=True)
        counts = {"total": len(group), "kept": 0, "fodder": 0}
        keepers = []
        fodder = []

        if len(group) <= 1:
            if group:
                keepers.append(group[0])
                counts["kept"] = 1
        else:
            best = group[0]
            keepers.append(best)
            counts["kept"] = 1
            for pal in group[1:]:
                fodder.append(pal)
                counts["fodder"] += 1
            for pal in fodder:
                suggestions.append({
                    "species": species,
                    "condense_into": {
                        "display_name": best.get("display_name", species),
                        "level": best.get("level", 1),
                        "rank": best.get("rank", 0),
                        "rank_label": TIER_NAMES.get(best.get("rank", 0), "None"),
                        "passives": best.get("passives", []),
                        "score": best.get("overall_score", 0)
                    },
                    "condense_from": {
                        "display_name": pal.get("display_name", species),
                        "level": pal.get("level", 1),
                        "rank": pal.get("rank", 0),
                        "rank_label": TIER_NAMES.get(pal.get("rank", 0), "None"),
                        "passives": pal.get("passives", []),
                        "score": pal.get("overall_score", 0),
                        "reason": _condense_reason(best, pal)
                    }
                })

        species_summary.append({
            "species": species,
            "total": counts["total"],
            "keepers": keepers,
            "fodder": fodder,
            "recommendation": f"Condense {counts['fodder']} into best {species}" if counts["fodder"] > 0 else "Only one, keep as is"
        })

    return {"suggestions": suggestions, "species_summary": species_summary}


def _condense_reason(best, fodder):
    reasons = []
    score_diff = best["overall_score"] - fodder["overall_score"]
    if score_diff > 50:
        reasons.append(f"Score {score_diff}pts higher")
    if len(best.get("passives", [])) > len(fodder.get("passives", [])):
        reasons.append("More/better passives")
    if best["level"] > fodder["level"] + 5:
        reasons.append(f"{best['level'] - fodder['level']} levels higher")
    if best.get("rank", 0) > fodder.get("rank", 0):
        reasons.append(f"Already rank {TIER_NAMES.get(best['rank'], '')}")
    if fodder.get("rank", 0) > 0:
        reasons.append(f"Rank {TIER_NAMES.get(fodder['rank'], '')} - good condense value")
    if not reasons:
        reasons.append("Lower overall value")
    return "; ".join(reasons)


# ═══════════════════════════════════════════════
#  CACHE LAYER
# ═══════════════════════════════════════════════

def _load_cache():
    global _cache
    if not os.path.isfile(_CACHE_PATH):
        return
    try:
        with open(_CACHE_PATH, "r") as f:
            data = json.load(f)
        with _cache_lock:
            _cache["last_updated"] = data.get("last_updated")
            _cache["players"] = data.get("players", {})
    except Exception as e:
        print(f"Failed to load cache: {e}", file=sys.stderr)


def _save_cache():
    with _cache_lock:
        data = {"last_updated": _cache["last_updated"], "players": _cache["players"]}
    tmp = _CACHE_PATH + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _CACHE_PATH)
    except Exception as e:
        print(f"Failed to write cache: {e}", file=sys.stderr)


def _rebuild_cache():
    with _cache_lock:
        if _cache["refreshing"]:
            return
        _cache["refreshing"] = True

    try:
        merged = {}
        sources = []

        # Phase 1: .sav (complete data, all boxes)
        sav = _read_sav_players()
        if sav:
            sources.append(f"sav({len(sav)})")
            merged.update(sav)

        # Phase 2: mod JSON (live party data, better passives)
        mod = _read_mod_json()
        if mod:
            sources.append(f"mod({len(mod)})")
            for sid, mplayer in mod.items():
                if sid in merged:
                    merged[sid] = _overlay_mod_pals(merged[sid], mplayer)
                    merged[sid]["name"]  = mplayer.get("name", merged[sid].get("name", sid))
                    merged[sid]["last_updated"] = datetime.now(timezone.utc).isoformat()
                else:
                    merged[sid] = mplayer

        if not merged:
            print("No data from any source", file=sys.stderr)
            return

        with _cache_lock:
            _cache["players"] = merged
            _cache["last_updated"] = datetime.now(timezone.utc).isoformat()
        _save_cache()
        total_pals = sum(len(p.get("pals", [])) for p in merged.values())
        print(f"Cache rebuilt: {len(merged)} players, {total_pals} pals from {' + '.join(sources)}", file=sys.stderr)
    except Exception as e:
        print(f"Cache rebuild failed: {e}", file=sys.stderr)
    finally:
        with _cache_lock:
            _cache["refreshing"] = False


def _get_cached_player(steam_id):
    with _cache_lock:
        return _cache["players"].get(steam_id)


def _get_all_cached_players():
    with _cache_lock:
        return list(_cache["players"].values())


def _cache_age_seconds():
    with _cache_lock:
        ts = _cache["last_updated"]
    if not ts:
        return None
    try:
        updated = datetime.fromisoformat(ts)
        return (datetime.now(timezone.utc) - updated).total_seconds()
    except Exception:
        return None


# ═══════════════════════════════════════════════
#  API ROUTES
# ═══════════════════════════════════════════════

@app.route("/api/players")
def list_players():
    age = _cache_age_seconds()
    if age is None:
        return jsonify({"players": [], "message": "No data. Trigger /api/refresh first."})
    with _cache_lock:
        cached_players = dict(_cache["players"])
    players = []
    for sid, data in cached_players.items():
        players.append({
            "steam_id":   data.get("steam_id", sid),
            "name":       data.get("name", sid),
            "player_name": data.get("player_name", data.get("name", sid)),
            "pal_count":  data.get("pal_count", len(data.get("pals", []))),
            "party_count": data.get("party_count", 0),
            "box_count":   data.get("box_count", 0),
            "nickname":   data.get("nickname", ""),
        })
    return jsonify({"players": players, "cache_age_seconds": age})


@app.route("/api/players/<steam_id>")
def player_detail(steam_id):
    data = _get_cached_player(steam_id)
    if data is None:
        return jsonify({"error": "Player not found in cache"}), 404
    return jsonify(data)


@app.route("/api/players/<steam_id>/suggestions")
def player_suggestions(steam_id):
    data = _get_cached_player(steam_id)
    if data is None:
        return jsonify({"error": "Player not found in cache"}), 404
    result = generate_suggestions(data.get("pals", []))
    result["player"] = {"steam_id": steam_id}
    result["pal_count"] = len(data.get("pals", []))
    return jsonify(result)


@app.route("/api/pals/search")
def search_pals():
    query = request.args.get("q", "").lower()
    limit = request.args.get("limit", 50, type=int)
    min_passive_score = request.args.get("min_score", 0, type=int)
    results = []
    for data in _get_all_cached_players():
        for pal in data.get("pals", []):
            name = pal.get("display_name", "").lower()
            if query and query not in name:
                continue
            if pal.get("passive_score", 0) < min_passive_score:
                continue
            pal["owner_steam_id"] = data.get("steam_id", "")
            results.append(pal)
    results.sort(key=lambda x: x.get("overall_score", 0), reverse=True)
    return jsonify({"pals": results[:limit], "total": len(results), "query": query})


@app.route("/api/cache/status")
def cache_status():
    age = _cache_age_seconds()
    with _cache_lock:
        player_count = len(_cache["players"])
        refreshing = _cache["refreshing"]
    return jsonify({
        "cached": age is not None,
        "cache_age_seconds": age,
        "player_count": player_count,
        "refreshing": refreshing,
        "sav_available": HAS_PAL_SAVE,
        "live_data_dir": LIVE_DATA_DIR,
        "save_dir": SAVE_DIR,
    })


@app.route("/_debug/explore")
def debug_explore():
    """Dump the GVAS property tree of a player's .sav file for debugging."""
    if not HAS_PAL_SAVE:
        return jsonify({"error": "palworld_save_tools not available"})
    steam_id = request.args.get("steam_id", "")
    if not steam_id:
        return jsonify({"error": "?steam_id=<player_steam_id>"})

    world_dirs = find_world_dirs()
    for wd in world_dirs:
        path = os.path.join(wd, "Players", f"{steam_id}.sav")
        if not os.path.isfile(path):
            path = os.path.join(wd, "Players", f"{steam_id.lower()}.sav")
        if os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    raw = f.read()
                gvas_bytes, _ = read_sav_gvas(raw)
                gvas_file = GvasFile.read(gvas_bytes, PALWORLD_TYPE_OVERRIDES, PALWORLD_SAVE_TYPE_OVERRIDES)

                def summarize(obj, depth=0):
                    if depth > 5:
                        return "..."
                    if isinstance(obj, dict):
                        keys = list(obj.keys())[:30]
                        return {k: type(v).__name__ if not isinstance(v, (dict, list)) else summarize(v, depth + 1) for k, v in list(obj.items())[:15]}
                    if isinstance(obj, list):
                        if not obj:
                            return "[]"
                        return f"[{len(obj)} items, first: {summarize(obj[0], depth + 1)}]"
                    if hasattr(obj, 'value'):
                        return f"<{type(obj).__name__}>"
                    return str(obj)[:100]

                tree = {k: summarize(v.value) if hasattr(v, 'value') else type(v).__name__ for k, v in gvas_file.properties.items()}
                return jsonify({"steam_id": steam_id, "property_tree": tree, "top_level_keys": list(gvas_file.properties.keys())})
            except Exception as e:
                return jsonify({"error": str(e)})
    return jsonify({"error": "Player .sav not found"})


@app.route("/api/refresh")
def refresh_cache():
    with _cache_lock:
        if _cache["refreshing"]:
            return jsonify({"status": "already_refreshing"})
    threading.Thread(target=_rebuild_cache, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "sav_parsing": HAS_PAL_SAVE,
    })


# ═══════════════════════════════════════════════
#  BACKGROUND REFRESH
# ═══════════════════════════════════════════════

def _start_background_refresh():
    while True:
        import time
        time.sleep(_CACHE_REFRESH_INTERVAL)
        age = _cache_age_seconds()
        if age is not None and age < _CACHE_REFRESH_INTERVAL * 0.8:
            continue
        _rebuild_cache()


if __name__ == "__main__":
    _load_cache()
    age = _cache_age_seconds()
    if age is None:
        print("No cache found, starting initial rebuild...", file=sys.stderr)
        threading.Thread(target=_rebuild_cache, daemon=True).start()
    elif age > _CACHE_REFRESH_INTERVAL:
        print(f"Cache is {age:.0f}s old, refreshing...", file=sys.stderr)
        threading.Thread(target=_rebuild_cache, daemon=True).start()
    else:
        print(f"Cache loaded ({age:.0f}s old, {len(_cache['players'])} players)", file=sys.stderr)
    threading.Thread(target=_start_background_refresh, daemon=True).start()
    port = int(os.environ.get("API_PORT", 8213))
    print(f"EZPAL API on port {port} (sav={HAS_PAL_SAVE})", file=sys.stderr)
    app.run(host="0.0.0.0", port=port, debug=False)
