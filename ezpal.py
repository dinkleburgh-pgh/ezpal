#!/opt/palapi/bin/python
"""EZPAL API - Reads live pal data from EZPALExporter mod JSON files."""
import os, sys, json, threading
from datetime import datetime, timezone
from flask import Flask, jsonify, request, render_template_string
from flask_cors import CORS
from collections import defaultdict

app = Flask(__name__)
CORS(app)

# --- Config ---
LIVE_DATA_DIR = os.environ.get("EZPAL_DATA_DIR") or "/var/lib/pelican/volumes/0ef8fccb-b40a-4843-b24d-35fdc0e52ba1/ezpal_live/"
_CACHE_PATH = os.path.join(os.path.dirname(__file__), "paldata.json")
_CACHE_REFRESH_INTERVAL = 30
_cache = {"last_updated": None, "players": {}, "refreshing": False}
_cache_lock = threading.Lock()
# --- End Config ---

PASSIVE_SCORES = {
    # Rainbow tier
    "Legend": 100, "Demon God": 95, "Diamond Body": 70, "Vampiric": 70,
    "Remarkable Craftsmanship": 120, "Swift": 70, "Lucky": 85,
    "Eternal Flame": 90, "Siren of the Void": 90, "Invader": 90,
    "Savior": 90, "Lunker": 90, "Eternal Engine": 90, "King of the Waves": 90,
    # T3 elemental (correct names)
    "Flame Emperor": 90, "Ice Emperor": 90, "Lord of the Sea": 90,
    "Lord of Lightning": 90, "Divine Dragon": 90, "Lord of the Underworld": 90,
    "Earth Emperor": 90, "Celestial Emperor": 90, "Spirit Emperor": 90,
    # T3 non-elemental
    "Ferocious": 80, "Burly Body": 75, "Artisan": 80, "Serenity": 70,
    "Vanguard": 65, "Stronghold Strategist": 60, "Philanthropist": 55,
    "Infinite Stamina": 70, "Ace Swimmer": 65, "Workaholic": 50,
    # T2
    "Musclehead": 80, "Serious": 60, "Runner": 55,
    # T1
    "Work Slave": 50, "Conceited": 35, "Nimble": 30, "Hooligan": 35,
    "Brave": 25, "Nocturnal": 30, "Impatient": 25, "Hard Skin": 25,
    # T1 elemental
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


# --- Enrichment ---

def score_passives(positives, negatives=None):
    """Score a pal's passives. Accepts positive and negative lists separately."""
    score = 0
    for p in (positives or []):
        score += PASSIVE_SCORES.get(p, 0)
    for p in (negatives or []):
        score -= 30  # any negative passive costs 30 pts
    return score


def calc_overall_score(pal):
    score = pal.get("passive_score", 0)
    score += pal.get("level", 1) * 2
    score += pal.get("rank", 0) * 20
    # Accept both field name conventions: talent_* (old) and *_iv (mod v2)
    hp  = pal.get("talent_hp",      pal.get("hp_iv",  0))
    atk = pal.get("talent_attack",  pal.get("atk_iv", 0))
    dfn = pal.get("talent_defense", pal.get("def_iv", 0))
    score += (hp + atk + dfn) / 3
    if pal.get("is_boss") or pal.get("is_alpha"):
        score += 15
    return round(score, 1)


def enrich_pal(pal):
    # ── Normalise species field (mod writes "species", old code used "character_id")
    species = pal.get("species") or pal.get("character_id") or "Unknown"
    pal["name"]         = species
    pal["display_name"] = PAL_NAMES.get(species, species.replace("_", " ").title())

    # ── Normalise passive fields
    # Mod writes separate positives/negatives; old format used "passives"
    positives = pal.get("passives") or pal.get("passives") or []
    negatives = pal.get("neg_passives") or []
    # Old format: everything in one list, negatives identified by name
    if not negatives and positives:
        negatives = [p for p in positives if p in NEGATIVE_PASSIVES]
        positives = [p for p in positives if p not in NEGATIVE_PASSIVES]
    pal["passives"]     = positives
    pal["neg_passives"] = negatives

    # ── Normalise IV fields
    pal["talent_hp"]       = pal.get("talent_hp",      pal.get("hp_iv",  0))
    pal["talent_attack"]   = pal.get("talent_attack",  pal.get("atk_iv", 0))
    pal["talent_defense"]  = pal.get("talent_defense", pal.get("def_iv", 0))

    pal["passive_score"] = score_passives(positives, negatives)
    pal["overall_score"] = calc_overall_score(pal)
    return pal


def enrich_player(player):
    # Normalise player identity fields (mod writes player_id/player_name)
    if not player.get("steam_id"):
        player["steam_id"] = player.get("player_id", "unknown")
    if not player.get("name"):
        player["name"] = player.get("player_name", player.get("nickname", player["steam_id"]))
    if "pals" in player:
        player["pals"] = [enrich_pal(p) for p in player.get("pals", []) if p]
    return player


# --- Suggestion Engine ---

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
                condense_needed = min(4 - best.get("rank", 0), pal.get("rank", 0) + 1)
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


# --- Cache Layer ---

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
        data = {
            "last_updated": _cache["last_updated"],
            "players": _cache["players"],
        }
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
        combined_path = os.path.join(LIVE_DATA_DIR, "all_players.json")
        if not os.path.isfile(combined_path):
            print("all_players.json not found yet", file=sys.stderr)
            return

        with open(combined_path, "r") as f:
            raw = json.load(f)

        players_list = raw.get("players", raw) if isinstance(raw, dict) else raw
        if isinstance(players_list, dict):
            players_list = list(players_list.values())

        new_players = {}
        for player in players_list:
            # Accept both "steam_id" (old) and "player_id" (mod v2)
            sid = player.get("steam_id") or player.get("player_id") or ""
            if not sid:
                print("WARNING: player entry has no steam_id or player_id, skipping", file=sys.stderr)
                continue
            enriched = enrich_player(player)
            new_players[sid] = enriched

        with _cache_lock:
            _cache["players"] = new_players
            _cache["last_updated"] = datetime.now(timezone.utc).isoformat()
        _save_cache()
        print(f"Cache rebuilt: {len(new_players)} players from JSON", file=sys.stderr)
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


# --- API Routes ---

@app.route("/api/players")
def list_players():
    age = _cache_age_seconds()
    if age is None:
        return jsonify({"players": [], "message": "No cache data. Trigger /api/refresh first."})

    with _cache_lock:
        cached_players = dict(_cache["players"])
    players = []
    for sid, data in cached_players.items():
        players.append({
            "steam_id":   data.get("steam_id", sid),
            "name":       data.get("name", data.get("player_name", sid)),
            "pal_count":  len(data.get("pals", [])),
            "last_updated": data.get("last_updated"),
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


@app.route("/api/refresh")
def refresh_cache():
    with _cache_lock:
        if _cache["refreshing"]:
            return jsonify({"status": "already_refreshing"})
    threading.Thread(target=_rebuild_cache, daemon=True).start()
    return jsonify({"status": "started"})


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
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# --- Background Refresh ---

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
    print(f"EZPAL API on port {port}", file=sys.stderr)
    app.run(host="0.0.0.0", port=port, debug=False)
