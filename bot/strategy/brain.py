"""
Strategy brain — main decision engine with priority-based action selection.
Implements the game-loop.md priority chain for high win rate.

v1.5.2 changes:
- Guardians now ATTACK player agents directly (hostile combatants)
- Curse is TEMPORARILY DISABLED (no whisper Q&A flow)
- Free room: 5 guardians (reduced from 30), each drops 120 sMoltz
- connectedRegions: either full Region objects OR bare string IDs — type-check!
- pendingDeathzones: entries are {id, name} objects

Uses ALL view fields from api-summary.md:
- self: agent stats, inventory, equipped weapon
- currentRegion: terrain, weather, connections, facilities
- connectedRegions: adjacent regions (full Region object when visible, bare string ID when out-of-vision)
- visibleRegions: all regions in vision range
- visibleAgents: other agents (players + guardians — guardians are HOSTILE)
- visibleMonsters: monsters
- visibleNPCs: NPCs (flavor — safe to ignore per game-systems.md)
- visibleItems: ground items in visible regions
- pendingDeathzones: regions becoming death zones next ({id, name} entries)
- recentLogs: recent gameplay events
- recentMessages: regional/private/broadcast messages
- aliveCount: remaining alive agents
"""
import requests
from bot.utils.logger import get_logger

log = get_logger(__name__)

# ── Weapon stats from combat-items.md ─────────────────────────────────
WEAPONS = {
    "fist": {"bonus": 0, "range": 0},
    "dagger": {"bonus": 10, "range": 0},
    "sword": {"bonus": 20, "range": 0},
    "katana": {"bonus": 35, "range": 0},
    "bow": {"bonus": 5, "range": 1},
    "pistol": {"bonus": 10, "range": 1},
    "sniper": {"bonus": 28, "range": 2},
}

WEAPON_PRIORITY = ["katana", "sniper", "sword", "pistol", "dagger", "bow", "fist"]

# ── Item priority for pickup ──────────────────────────────────────────
# Moltz = ALWAYS pickup (highest). Weapons > healing > utility.
# Binoculars = passive (vision+1 just by holding), always pickup.
ITEM_PRIORITY = {
    "rewards": 300,  # Moltz/sMoltz — ALWAYS pickup first
    "katana": 100, "sniper": 95, "sword": 90, "pistol": 85,
    "dagger": 80, "bow": 75,
    "medkit": 70, "bandage": 65, "emergency_food": 60, "energy_drink": 58,
    "binoculars": 55,  # Passive: vision +1 permanent, always pickup
    "map": 52,          # Use immediately to reveal entire map
    "megaphone": 40,
}

# ── Recovery items for healing (combat-items.md) ──────────────────────
# For normal healing (HP<70): prefer Emergency Food (save Bandage/Medkit)
# For critical healing (HP<30): prefer Bandage then Medkit
RECOVERY_ITEMS = {
    "medkit": 50, "bandage": 30, "emergency_food": 20,
    "energy_drink": 0,  # EP restore, not HP
}

# Weather combat penalty per game-systems.md
WEATHER_COMBAT_PENALTY = {
    "clear": 0.0,
    "rain": 0.05,   # -5%
    "fog": 0.10,    # -10%
    "storm": 0.15,  # -15%
}


def solve_captcha(question: str) -> str:
    """AI Captcha Solver menggunakan Llama-3."""
    try:
        # Placeholder: ganti dengan API call ke Llama-3
        # Contoh: response = requests.post("https://api.llama3.com/solve", json={"question": question})
        # return response.json().get("answer", "")
        log.info("CAPTCHA: Solving '%s'", question)
        # Simulasi jawaban sederhana
        if "color" in question.lower():
            return "blue"  # Placeholder
        return "answer"  # Placeholder
    except Exception as e:
        log.error("CAPTCHA solve failed: %s", e)
        return ""


def calc_damage(atk: int, weapon_bonus: int, target_def: int,
                weather: str = "clear") -> int:
    """Damage formula per combat-items.md + game-systems.md weather penalty.
    Base: ATK + bonus - (DEF * 0.5), min 1.
    Weather: clear=0%, rain=-5%, fog=-10%, storm=-15%.
    """
    base = atk + weapon_bonus - int(target_def * 0.5)
    penalty = WEATHER_COMBAT_PENALTY.get(weather, 0.0)
    return max(1, int(base * (1 - penalty)))


def get_weapon_bonus(equipped_weapon) -> int:
    """Get ATK bonus from equipped weapon."""
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def get_weapon_range(equipped_weapon) -> int:
    """Get range from equipped weapon."""
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("range", 0)

_known_agents: dict = {}
# Map knowledge: track all revealed DZ/pending DZ/safe regions after using Map
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_path_history: list = []


def _resolve_region(entry, view: dict):
    """Resolve a connectedRegions entry to a full region object.
    Per v1.5.2 gotchas.md §3: entries are EITHER full Region objects
    (when adjacent region is within vision) OR bare string IDs (when out-of-vision).
    Returns the full object, or None if out-of-vision.
    """
    if isinstance(entry, dict):
        return entry  # Full object
    if isinstance(entry, str):
        # Look up in visibleRegions
        for r in view.get("visibleRegions", []):
            if isinstance(r, dict) and r.get("id") == entry:
                return r
    return None  # Out-of-vision — only ID is known


def _get_region_id(entry) -> str:
    """Extract region ID from either a string or dict entry."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("id", "")
    return ""


def reset_game_state():
    """Reset per-game tracking state. Call when game ends."""
    global _known_agents, _map_knowledge, _path_history
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _path_history.clear()
    log.info("Strategy brain reset for new game")


def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    Main decision engine. Returns action dict or None (wait).

    Priority chain per game-loop.md §3 (v1.5.2):
    1. DEATHZONE ESCAPE (overrides everything — 1.34 HP/sec!)
    1b. Pre-escape pending death zone
    2. [DISABLED] Curse resolution — curse temporarily disabled in v1.5.2
    2b. Guardian threat evasion (guardians now attack players!)
    3. Critical healing
    3b. Use utility items (Map, Energy Drink)
    4. Free actions (pickup, equip)
    5. Guardian farming (120 sMoltz per kill — only 5 guardians!)
    6. Favorable agent combat
    7. Monster farming
    8. Facility interaction
    9. Strategic movement (NEVER into DZ or pending DZ)
    10. Rest

    Uses ALL api-summary.md view fields for decision making.
    """
    self_data = view.get("self", {})
    region = view.get("currentRegion", {})
    hp = self_data.get("hp", 100)
    ep = self_data.get("ep", 10)
    max_ep = self_data.get("maxEp", 10)
    atk = self_data.get("atk", 10)
    defense = self_data.get("def", 5)
    is_alive = self_data.get("isAlive", True)
    inventory = self_data.get("inventory", [])
    equipped = self_data.get("equippedWeapon")

    # View-level fields per api-summary.md
    visible_agents = view.get("visibleAgents", [])
    visible_monsters = view.get("visibleMonsters", [])
    visible_npcs = view.get("visibleNPCs", [])
    visible_items_raw = view.get("visibleItems", [])
    # Unwrap: each visibleItem is { regionId, item: { id, name, typeId, ... } }
    visible_items = []
    for entry in visible_items_raw:
        if not isinstance(entry, dict):
            continue
        inner = entry.get("item")
        if isinstance(inner, dict):
            inner["regionId"] = entry.get("regionId", "")
            visible_items.append(inner)
        elif entry.get("id"):
            visible_items.append(entry)  # Legacy flat format
    visible_regions = view.get("visibleRegions", [])
    connected_regions = view.get("connectedRegions", [])
    pending_dz = view.get("pendingDeathzones", [])
    recent_logs = view.get("recentLogs", [])
    messages = view.get("recentMessages", [])
    alive_count = view.get("aliveCount", 100)

    # Fallback connections from currentRegion if connectedRegions empty
    connections = connected_regions or region.get("connections", [])
    interactables = region.get("interactables", [])
    region_id = region.get("id", "")
    region_terrain = region.get("terrain", "").lower() if isinstance(region, dict) else ""
    region_weather = region.get("weather", "").lower() if isinstance(region, dict) else ""

    if not is_alive:
        return None  # Dead — wait for game_ended

    # Sistem Jejak Kaki: Tambah region_id ke history
    global _path_history
    if not _path_history or _path_history[-1] != region_id:
        _path_history.append(region_id)
        if len(_path_history) > 10:
            _path_history.pop(0)

    # ── Build FULL danger map (DZ + pending DZ) ───────────────────
    # Used by ALL movement decisions to NEVER move into danger.
    # v1.5.2: pendingDeathzones entries are {id, name} objects
    danger_ids = set()
    for dz in pending_dz:
        if isinstance(dz, dict):
            danger_ids.add(dz.get("id", ""))
        elif isinstance(dz, str):
            danger_ids.add(dz)  # Legacy fallback
    # Also mark currently-active death zones from connected regions
    for conn in connections:
        resolved = _resolve_region(conn, view)
        if resolved and resolved.get("isDeathZone"):
            danger_ids.add(resolved.get("id", ""))

    # Track visible agents for memory
    _track_agents(visible_agents, self_data.get("id", ""), region_id)

    # ── Priority 1: DEATHZONE ESCAPE (overrides everything) ───────
    # Per game-systems.md: 1.34 HP/sec damage — bot dies fast!
    move_ep_cost = _get_move_ep_cost(region_terrain, region_weather)
    if region.get("isDeathZone", False):
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("🚨 IN DEATH ZONE! Escaping to %s (HP=%d)", safe, hp)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"ESCAPE: In death zone! HP={hp} dropping fast (1.34/sec)"}
        elif not safe:
            log.error("🚨 IN DEATH ZONE but NO SAFE REGION! All neighbors are DZ!")

    # ── Priority 1b: Pre-escape pending death zone ────────────────
    if region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("⚠️ Region %s becoming DZ soon! Escaping to %s", region_id[:8], safe)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "PRE-ESCAPE: Region becoming death zone soon"}

    # ── Priority 2: Curse resolution — DISABLED in v1.5.2 ─────────
    # Curse is temporarily disabled. Guardians no longer curse players.
    # Legacy code kept inert — will re-enable when curse returns.
    # (was: _check_curse → whisper answer to guardian)

    # ── Priority 2b: Guardian threat evasion (v1.5.2) ─────────────
    # Guardians now ATTACK player agents directly! Flee if low HP.
    guardians_here = [a for a in visible_agents
                      if a.get("isGuardian", False) and a.get("isAlive", True)
                      and a.get("regionId") == region_id]
    if guardians_here and hp < 40 and ep >= move_ep_cost:
        # Low HP + guardian in same region = flee!
        safe = _find_safe_region(connections, danger_ids, view)
        if safe:
            log.warning("⚠️ Guardian threat! HP=%d, fleeing to safety", hp)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"GUARDIAN FLEE: HP={hp}, guardian in region, too dangerous"}

    # ── Priority 2c: Emergency flee if enemy can’t be hurt or if unarmed ───
    opponents_here = [a for a in visible_agents
                      if a.get("isAlive", True)
                      and a.get("id") != self_data.get("id")
                      and a.get("regionId") == region_id
                      and not _is_ally(a)]
    unarmed = not equipped or (isinstance(equipped, dict)
                                and equipped.get("typeId", "").lower() == "fist")
    if opponents_here and (unarmed or any(
            calc_damage(atk, get_weapon_bonus(equipped), enemy.get("def", 5), region_weather) <= 0
            for enemy in opponents_here)):
        safe = _find_safe_region(connections, danger_ids, view)
        if safe:
            log.warning("🚨 EMERGENCY FLEE! Enemy present and ineffective weapon/unarmed")
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "EMERGENCY FLEE: enemy in same region and unable to fight safely"}

    # ── FREE ACTIONS (no cooldown, do before main action) ─────────

    # Auto-pickup Moltz and valuable items in vacuum mode — pick all desired items immediately
    pickup_action = _check_vacuum_pickup(visible_items, inventory, region_id, equipped, memory_temp)
    if pickup_action:
        return pickup_action

    # Auto-equip better weapon
    equip_action = _check_equip(inventory, equipped)
    if equip_action:
        return equip_action

    # Use utility items: Map (reveal map), Megaphone (broadcast)
    util_action = _use_utility_item(inventory, hp, ep, alive_count)
    if util_action:
        return util_action

    # If cooldown active, only free actions allowed
    if not can_act:
        return None

    # (Death zone escape already handled above as Priority 1)

    # ── Priority 3: Healing management ─────────────────────────────
    # HP < 30 = CRITICAL: use Bandage first (30 HP), then Medkit (50 HP)
    # HP < 70 = MODERATE: use Emergency Food first (20 HP), save better items
    if hp < 30:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}, using {heal.get('typeId', 'heal')}"}
    elif hp < 70:
        heal = _find_healing_item(inventory, critical=False)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}, using {heal.get('typeId', 'heal')}"}

    # ── Priority 4: EP recovery if cursed (EP=0) or very low ──────
    if ep == 0:
        # Check for energy drink first
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": "EP RECOVERY: EP=0, using energy drink (+5 EP)"}

    # ── Priority 5: Guardian farming (v1.5.2: 120 sMoltz per kill!) ─
    # Only 5 guardians per free room — each worth 120 sMoltz!
    # Guardians now ATTACK back — only fight if we can win.
    guardians = [a for a in visible_agents
                 if a.get("isGuardian", False) and a.get("isAlive", True)]
    if guardians and ep >= 2 and hp >= 35:
        target = _select_weakest(guardians)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            # v1.5.2: guardians fight back — check if we can take them
            my_dmg = calc_damage(atk, get_weapon_bonus(equipped),
                                target.get("def", 5), region_weather)
            guardian_dmg = calc_damage(target.get("atk", 10),
                                       _estimate_enemy_weapon_bonus(target),
                                       defense, region_weather)
            # Fight if we deal more damage OR target is low HP (finish off)
            if my_dmg >= guardian_dmg or target.get("hp", 100) <= my_dmg * 3:
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"GUARDIAN FARM: HP={target.get('hp','?')} "
                                  f"(120 sMoltz! dmg={my_dmg} vs {guardian_dmg})"}

    # ── Priority 6: Favorable agent combat ────────────────────────
    # Aggressive Mafia playstyle: gank with allies, vulture low-HP targets,
    # execute when we can finish, otherwise fall back to movement.
    enemies = [a for a in visible_agents
               if not a.get("isGuardian", False) and a.get("isAlive", True)
               and a.get("id") != self_data.get("id")
               and not _is_ally(a)]  # Protokol Aliansi: skip peaxel
    if enemies and ep >= 2:
        w_range = get_weapon_range(equipped)
        allies_here = [a for a in visible_agents
                       if a.get("isAlive", True)
                       and _is_ally(a)
                       and a.get("regionId") == region_id]
        same_region_enemies = [e for e in enemies if e.get("regionId") == region_id]

        if same_region_enemies:
            target = _select_weakest(same_region_enemies)
            if allies_here:
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": "GANKING MAFIA! Keroyok musuh!"}
            if target.get("hp", 0) <= 40:
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": "VULTURE MODE! Nyampah kill!"}
            my_dmg = calc_damage(atk, get_weapon_bonus(equipped),
                                 target.get("def", 5), region_weather)
            if hp > 85 or target.get("hp", 0) <= my_dmg:
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": "Eksekusi musuh!"}
        else:
            ranged_enemies = [e for e in enemies
                              if _is_in_range(e, region_id, w_range, connections)]
            if ranged_enemies:
                target = _select_weakest(ranged_enemies)
                if target.get("hp", 0) <= 40:
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": "VULTURE MODE! Nyampah kill!"}
                my_dmg = calc_damage(atk, get_weapon_bonus(equipped),
                                     target.get("def", 5), region_weather)
                if hp > 85 or target.get("hp", 0) <= my_dmg:
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": "Eksekusi musuh!"}

    # ── Priority 7: Monster farming ───────────────────────────────
    if not region.get("isDeathZone", False) and region_id not in danger_ids:
        monsters = [m for m in visible_monsters if m.get("hp", 0) > 60]
    else:
        monsters = []
    if monsters and ep >= 2:
        target = _select_weakest(monsters)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            my_dmg = calc_damage(atk, get_weapon_bonus(equipped), target.get("def", 5), region_weather)
            if my_dmg > 0:
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "monster"},
                        "reason": f"PREMAN PASAR! Bantai monster! HP={target.get('hp', '?')} dmg={my_dmg}"}
            log.warning("MONSTER FARM skipped: my_dmg=%s <= 0 against %s", my_dmg, target.get('name', 'monster'))

    # ── Priority 7b: Moderate healing (HP < 70, safe area) ────────
    if hp < 70 and not enemies:
        heal = _find_healing_item(inventory, critical=(hp < 30))
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}, area safe, using {heal.get('typeId', 'heal')}"}

    # ── Priority 8: Facility interaction ──────────────────────────
    if interactables and ep >= 2 and not region.get("isDeathZone"):
        facility = _select_facility(interactables, hp, ep)
        if facility:
            return {"action": "interact",
                    "data": {"interactableId": facility["id"]},
                    "reason": f"FACILITY: {facility.get('type', 'unknown')}"}

    # ── Priority 9: Strategic movement ────────────────────────────
    # Use connectedRegions — NEVER move into DZ or pending DZ!
    if ep >= move_ep_cost and connections:
        move_target = _choose_move_target(connections, danger_ids,
                                           region, visible_items, alive_count, memory_temp)
        if move_target:
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": "EXPLORE: Moving to better position"}

    # ── Priority 10: Rest (EP < 4 and safe) ───────────────────────
    if ep < 4 and not enemies and not region.get("isDeathZone") and region_id not in danger_ids:
        return {"action": "rest", "data": {},
                "reason": f"REST: EP={ep}/{max_ep}, area is safe (+1 bonus EP)"}

    return None  # Wait for next turn


# ── Helper functions ──────────────────────────────────────────────────

def _is_ally(agent: dict) -> bool:
    """Protokol Aliansi: Cek apakah agent adalah sekutu (nama mengandung 'peaxel')."""
    name = agent.get("name", "").lower()
    return "peaxel" in name


def _get_move_ep_cost(terrain: str, weather: str) -> int:
    """Calculate move EP cost per game-systems.md.
    Base: 2 EP per move.
    - Water terrain costs 3 EP.
    - Storm costs 3 EP.
    """
    terrain = (terrain or "").lower()
    weather = (weather or "").lower()
    if terrain == "water":
        return 3
    if weather == "storm":
        return 3  # 2 base + 1 storm
    return 2


def _estimate_enemy_weapon_bonus(agent: dict) -> int:
    """Estimate enemy's weapon bonus from their equipped weapon."""
    weapon = agent.get("equippedWeapon")
    if not weapon:
        return 0
    type_id = weapon.get("typeId", "").lower() if isinstance(weapon, dict) else ""
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def _is_weapon_like(item: dict) -> bool:
    """Detect weapon-like items even if category metadata is missing."""
    if not isinstance(item, dict):
        return False
    type_id = (item.get("typeId") or "").lower()
    name = (item.get("name") or "").lower()
    weapon_keywords = ["dagger", "knife", "sword", "katana", "pistol",
                       "sniper", "bow", "rifle", "gun"]
    return any(keyword in type_id or keyword in name for keyword in weapon_keywords)


# Track observed agents for memory (threat assessment)
_known_agents: dict = {}


# ── CURSE HANDLING — DISABLED in v1.5.2 ───────────────────────────────
# Curse is temporarily disabled per strategy.md v1.5.2.
# Guardians no longer set victim EP to 0 and no whisper-question/answer flow.
# Legacy code kept below for reference — will re-enable when curse returns.
#
# def _check_curse(messages, my_id) -> dict | None:
#     """DISABLED: Guardian curse is temporarily disabled in v1.5.2."""
#     return None
#
# def _solve_curse_question(question) -> str:
#     """DISABLED: Guardian curse is temporarily disabled in v1.5.2."""
#     return ""


def _check_vacuum_pickup(items: list, inventory: list, equipped, region_id: str, memory_temp=None) -> dict | None:
    """Vacuum pickup: queue all desirable items in the current region.
    Free pickup/equip actions cost 0 EP and have no cooldown, so we can
    perform multiple sequential pickups without waiting for next turn.
    """
    if len(inventory) >= 10:
        return None

    local_items = [i for i in items if isinstance(i, dict) and i.get("regionId") == region_id]
    if not local_items:
        local_items = [i for i in items if isinstance(i, dict) and i.get("id")]
    if not local_items:
        return None

    if memory_temp and hasattr(memory_temp, 'is_junk_blacklisted'):
        local_items = [i for i in local_items if not memory_temp.is_junk_blacklisted(i.get("id", ""))]

    heal_count = sum(1 for i in inventory if isinstance(i, dict)
                     and i.get("typeId", "").lower() in RECOVERY_ITEMS
                     and RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0) > 0)

    scored = [(i, _pickup_score(i, inventory, heal_count, equipped)) for i in local_items]
    scored = [pair for pair in scored if pair[1] > 0]
    if not scored:
        return None

    scored.sort(key=lambda pair: pair[1], reverse=True)
    item_ids = []
    inventory_count = len(inventory)
    for item, score in scored:
        if inventory_count >= 10:
            break
        item_id = item.get("id")
        if not item_id:
            continue
        item_ids.append(item_id)
        inventory_count += 1

    if not item_ids:
        return None

    log.info("VACUUM PICKUP: %d items queued for pickup", len(item_ids))
    return {"action": "vacuum_pickup", "data": {"itemIds": item_ids},
            "reason": f"VACUUM PICKUP: collecting {len(item_ids)} nearby items"}


def _check_pickup(items: list, inventory: list, equipped, region_id: str, memory_temp=None) -> dict | None:
    """Smart pickup: weapons > healing stockpile > utility > Moltz (always).
    Max inventory = 10 per limits.md.
    Strategy:
    - Moltz ($rewards): ALWAYS pickup, highest priority
    - Weapons: pickup if better than current OR no weapon equipped
    - Healing: stockpile for endgame (keep at least 2-3 healing items)
    - Binoculars: passive vision+1, always pickup
    - Map: pickup and use immediately
    """
    if len(inventory) >= 10:
        return None
    # Filter items in current region (items may lack regionId field)
    local_items = [i for i in items
                   if isinstance(i, dict) and i.get("regionId") == region_id]
    # Fallback: if regionId filter found nothing, use all visible items
    # (the game may not set regionId on item objects)
    if not local_items:
        local_items = [i for i in items if isinstance(i, dict) and i.get("id")]
    if not local_items:
        return None

    # Memori Sampah: Filter out blacklisted items
    if memory_temp and hasattr(memory_temp, 'is_junk_blacklisted'):
        local_items = [i for i in local_items if not memory_temp.is_junk_blacklisted(i.get("id", ""))]

    # Count current healing items for stockpile management
    heal_count = sum(1 for i in inventory if isinstance(i, dict)
                     and i.get("typeId", "").lower() in RECOVERY_ITEMS
                     and RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0) > 0)

    # Sort by priority — Moltz always first
    local_items.sort(
        key=lambda i: _pickup_score_wrapper(i, inventory, equipped), reverse=True)
    best = local_items[0]
    score = _pickup_score_wrapper(best, inventory, equipped)
    if score > 0:
        type_id = best.get('typeId', 'item')
        log.info("PICKUP: %s (score=%d, heal_stock=%d)", type_id, score, heal_count)
        return {"action": "pickup", "data": {"itemId": best["id"]},
                "reason": f"PICKUP: {type_id}"}
    return None


def _pickup_score(self, item: dict, current_best: dict | None) -> int:
    """Sistem Vulture Looting - Maling Rakus by Mandor!"""
    type_id = item.get("typeId", "").lower()
    if not type_id:
        type_id = item.get("type", "").lower()
        
    # CEK KAPASITAS TAS DULU (Kalau udah 10, STOP mungut biar gak bug)
    inv = self.agent_state.get("inventory", [])
    if len(inv) >= 10:
        return 0 

    # 1. UANG ADALAH RAJA (Moltz / Reward)
    if "moltz" in type_id or "reward" in type_id:
        return 500
        
    # 2. OBAT & STAMINA WAJIB DIAMBIL
    consumables = ["medkit", "bandage", "energy_drink", "food", "ration"]
    if any(c in type_id for c in consumables):
        return 400
        
    # 3. SENJATA (Sita dari musuh!)
    weapons = ["dagger", "sword", "katana", "pistol", "sniper", "bow", "rifle", "gun"]
    is_weapon = any(w in type_id for w in weapons) or item.get("category", "").lower() == "weapon"
    if is_weapon:
        # Kalau tangan kosong, wajib sikat!
        if not current_best or current_best.get("typeId") == "fist":
            return 300 
        else:
            # Walaupun udah punya senjata bagus, tetep ambil (skor kecil) biar musuh gak kebagian!
            return 10
            
    # 4. BARANG UNIK (Jangan numpuk)
    utilities = ["map", "binoculars", "megaphone", "radio"]
    if any(u in type_id for u in utilities):
        # Cek apakah di tas udah ada barang ini
        has_item = any(i.get("typeId", "").lower() == type_id for i in inv)
        if has_item:
            return 0 # Udah punya, lewatin
        return 100 # Belum punya, sikat!
        
    # 5. BARANG LAINNYA / TIDAK DIKENAL
    # Kasih skor 5 agar tetep dipungut selama tas belum penuh 10/10
    return 5


def _pickup_score_wrapper(item: dict, inventory: list, equipped) -> int:
    class AgentState:
        def __init__(self, inventory):
            self.agent_state = {"inventory": inventory}

    current_best = equipped if isinstance(equipped, dict) else {"typeId": "fist"}
    return _pickup_score(AgentState(inventory), item, current_best)


def _check_equip(inventory: list, equipped) -> dict | None:
    """Auto-equip best weapon from inventory."""
    current_bonus = get_weapon_bonus(equipped) if equipped else 0
    best = None
    best_bonus = current_bonus
    for item in inventory:
        if not isinstance(item, dict):
            continue
        if item.get("category") == "weapon":
            type_id = item.get("typeId", "").lower()
            bonus = WEAPONS.get(type_id, {}).get("bonus", 0)
            if bonus > best_bonus:
                best = item
                best_bonus = bonus
    if best:
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"EQUIP: {best.get('typeId', 'weapon')} (+{best_bonus} ATK)"}
    return None


def _find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
    """Find nearest connected region that's NOT a death zone AND NOT pending DZ.
    Per v1.5.2 gotchas.md §3: connectedRegions entries are EITHER full Region objects
    (when visible) OR bare string IDs (when out-of-vision). Use _resolve_region().
    danger_ids = set of all DZ + pending DZ region IDs.
    """
    safe_regions = []
    for conn in connections:
        if isinstance(conn, str):
            if conn not in danger_ids:
                safe_regions.append((conn, 0))
        elif isinstance(conn, dict):
            rid = conn.get("id", "")
            is_dz = conn.get("isDeathZone", False)
            if rid and not is_dz and rid not in danger_ids:
                terrain = conn.get("terrain", "").lower()
                score = {"hills": 3, "plains": 2, "ruins": 1, "forest": 0, "water": -2}.get(terrain, 0)
                safe_regions.append((rid, score))

    if safe_regions:
        safe_regions.sort(key=lambda x: x[1], reverse=True)
        chosen = safe_regions[0][0]
        log.debug("Safe region selected: %s (score=%d, %d candidates)",
                  chosen[:8], safe_regions[0][1], len(safe_regions))
        return chosen

    # Last resort: any non-DZ connection (even if pending)
    for conn in connections:
        rid = conn if isinstance(conn, str) else conn.get("id", "")
        is_dz = conn.get("isDeathZone", False) if isinstance(conn, dict) else False
        if rid and not is_dz:
            log.warning("No fully safe region! Using fallback: %s", rid[:8])
            return rid
    return None


def _find_healing_item(inventory: list, critical: bool = False) -> dict | None:
    """Find best healing item based on urgency.
    critical=True (HP<30): prefer Bandage(30) then Medkit(50) — big heals first
    critical=False (HP<70): prefer Emergency Food(20) — save big heals for later
    """
    heals = []
    for i in inventory:
        if not isinstance(i, dict):
            continue
        type_id = i.get("typeId", "").lower()
        if type_id in RECOVERY_ITEMS and RECOVERY_ITEMS[type_id] > 0:
            heals.append(i)
    if not heals:
        return None

    if critical:
        # Critical: use biggest heal first (Medkit > Bandage > Emergency Food)
        heals.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0), reverse=True)
    else:
        # Normal: use smallest heal first (Emergency Food first, save big heals)
        heals.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0))
    return heals[0]


def _find_energy_drink(inventory: list) -> dict | None:
    """Find energy drink for EP recovery (+5 EP per combat-items.md)."""
    for i in inventory:
        if isinstance(i, dict) and i.get("typeId", "").lower() == "energy_drink":
            return i
    return None


def _select_weakest(targets: list) -> dict:
    """Select target with lowest HP."""
    return min(targets, key=lambda t: t.get("hp", 999))


def _is_in_range(target: dict, my_region: str, weapon_range: int,
                  connections=None) -> bool:
    """Check if target is in weapon range.
    Per combat-items.md: melee = same region, ranged = 1-2 regions.
    """
    target_region = target.get("regionId", "")

    # No regionId on target — assume same region (visible agents in same region)
    if not target_region:
        return True

    if target_region == my_region:
        return True  # Same region — melee and ranged both work

    if weapon_range >= 1 and connections:
        # Check if target is in an adjacent region (range 1+)
        adj_ids = set()
        for conn in connections:
            if isinstance(conn, str):
                adj_ids.add(conn)
            elif isinstance(conn, dict):
                adj_ids.add(conn.get("id", ""))
        if target_region in adj_ids:
            return True

    # Target is out of weapon range
    return False


def _select_facility(interactables: list, hp: int, ep: int) -> dict | None:
    """Select best facility to interact with per game-systems.md.
    Facilities: supply_cache, medical_facility, watchtower, broadcast_station, cave.
    """
    for fac in interactables:
        if not isinstance(fac, dict):
            continue
        if fac.get("isUsed"):
            continue
        ftype = fac.get("type", "").lower()
        # Priority: medical (if HP < 80) > supply_cache > watchtower > broadcast_station
        if ftype == "medical_facility" and hp < 80:
            return fac
        if ftype == "supply_cache":
            return fac
        if ftype == "watchtower":
            return fac
        if ftype == "broadcast_station":
            return fac
    return None


def _track_agents(visible_agents: list, my_id: str, my_region: str):
    """Track observed agents for threat assessment (agent-memory.md temp.knownAgents)."""
    global _known_agents
    for agent in visible_agents:
        if not isinstance(agent, dict):
            continue
        aid = agent.get("id", "")
        if not aid or aid == my_id:
            continue
        _known_agents[aid] = {
            "hp": agent.get("hp", 100),
            "atk": agent.get("atk", 10),
            "isGuardian": agent.get("isGuardian", False),
            "equippedWeapon": agent.get("equippedWeapon"),
            "lastSeen": my_region,
            "isAlive": agent.get("isAlive", True),
        }
    # Limit size
    if len(_known_agents) > 50:
        # Remove dead agents first
        dead = [k for k, v in _known_agents.items() if not v.get("isAlive", True)]
        for d in dead:
            del _known_agents[d]


def _use_utility_item(inventory: list, hp: int, ep: int, alive_count: int) -> dict | None:
    """Use utility items immediately after pickup.
    Map: reveals entire map → triggers _learn_from_map next view.
    Binoculars: PASSIVE (vision+1 just by holding) — no use_item needed.
    """
    for item in inventory:
        if not isinstance(item, dict):
            continue
        type_id = item.get("typeId", "").lower()
        # Map: use immediately to reveal entire map
        if type_id == "map":
            log.info("🗺️ Using Map! Will reveal entire map for strategic learning.")
            return {"action": "use_item", "data": {"itemId": item["id"]},
                    "reason": "UTILITY: Using Map — reveals entire map for DZ tracking"}
    return None


def learn_from_map(view: dict):
    """Called after Map is used — learn entire map layout.
    Track all death zones, pending DZ, and find safe center regions.
    Per game-guide.md: Map reveals entire map (1-time consumable).
    """
    global _map_knowledge
    visible_regions = view.get("visibleRegions", [])
    if not visible_regions:
        return

    _map_knowledge["revealed"] = True
    safe_regions = []

    for region in visible_regions:
        if not isinstance(region, dict):
            continue
        rid = region.get("id", "")
        if not rid:
            continue

        if region.get("isDeathZone"):
            _map_knowledge["death_zones"].add(rid)
        else:
            # Count connections — center regions have more connections
            conns = region.get("connections", [])
            terrain = region.get("terrain", "").lower()
            terrain_value = {"hills": 3, "plains": 2, "ruins": 2, "forest": 1, "water": -1}.get(terrain, 0)
            score = len(conns) + terrain_value
            safe_regions.append((rid, score))

    # Sort by connectivity+terrain — highest = most likely center
    safe_regions.sort(key=lambda x: x[1], reverse=True)
    _map_knowledge["safe_center"] = [r[0] for r in safe_regions[:5]]

    log.info("🗺️ MAP LEARNED: %d DZ regions, %d safe regions, top center: %s",
             len(_map_knowledge["death_zones"]),
             len(safe_regions),
             _map_knowledge["safe_center"][:3])


def _choose_move_target(connections, danger_ids: set,
                         current_region: dict, visible_items: list,
                         alive_count: int, memory_temp=None) -> str | None:
    """Choose best region to move to.
    CRITICAL: NEVER move into a death zone or pending death zone!
    Sistem Jejak Kaki: Hindari region yang sudah dikunjungi (10 terakhir).
    """
    candidates = []

    # Sistem Jejak Kaki: Ambil history path
    global _path_history
    path_history = set(_path_history)

    # Build set of regions with visible items for attraction
    item_regions = set()
    for item in visible_items:
        if isinstance(item, dict):
            item_regions.add(item.get("regionId", ""))

    for conn in connections:
        if isinstance(conn, str):
            # HARD BLOCK: never move into danger zone
            if conn in danger_ids:
                continue
            score = 1
            if conn in path_history:
                score -= 50
            if conn in item_regions:
                score += 5
            candidates.append((conn, score))

        elif isinstance(conn, dict):
            rid = conn.get("id", "")
            # HARD BLOCK: never move into DZ or pending DZ
            if not rid or conn.get("isDeathZone") or rid in danger_ids:
                continue
            score = 0
            if rid in path_history:
                score -= 50
            terrain = conn.get("terrain", "").lower()

            # Terrain scoring per game-systems.md
            terrain_scores = {
                "hills": 4, "plains": 2, "ruins": 2,
                "forest": 1, "water": -3,
            }
            score += terrain_scores.get(terrain, 0)

            if rid in item_regions:
                score += 5

            # Facilities attract
            facs = conn.get("interactables", [])
            if facs:
                unused = [f for f in facs if isinstance(f, dict) and not f.get("isUsed")]
                score += len(unused) * 2

            # Avoid weather penalties
            weather = conn.get("weather", "").lower()
            weather_penalty = {"storm": -2, "fog": -1, "rain": 0, "clear": 1}
            score += weather_penalty.get(weather, 0)

            # Late game: strong bonus for safe regions
            if alive_count < 30:
                score += 3

            # MAP KNOWLEDGE: prefer center regions learned from Map
            if _map_knowledge.get("revealed") and rid in _map_knowledge.get("safe_center", []):
                score += 5  # Strong pull toward center

            # MAP KNOWLEDGE: avoid known death zones
            if rid in _map_knowledge.get("death_zones", set()):
                continue  # HARD BLOCK

            candidates.append((rid, score))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]
"""
View fields from api-summary.md (all implemented above — v1.5.2):
✅ self          — hp, ep, atk, def, inventory, equippedWeapon, isAlive
✅ currentRegion — id, name, terrain, weather, connections, interactables, isDeathZone
✅ connectedRegions — full Region objects OR bare string IDs (type-safe via _resolve_region)
✅ visibleRegions  — used for connectedRegions fallback + region ID lookup
✅ visibleAgents   — guardians (HOSTILE!) + enemies + combat targeting
✅ visibleMonsters — monster farming targets
✅ visibleNPCs     — acknowledged (NPCs are flavor per game-systems.md)
✅ visibleItems    — pickup + movement attraction scoring
✅ pendingDeathzones — {id, name} entries for death zone escape + movement planning
✅ recentLogs      — available for analysis
✅ recentMessages  — communication (curse disabled in v1.5.2)
✅ aliveCount      — adaptive aggression (late game adjustment)
"""
