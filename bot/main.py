"""
Molty Royale AI Agent — Entry Point v2.0.
Run: python -m bot.main
Dashboard + Bot run concurrently.
"""
import asyncio
import os
import sys
from bot.heartbeat import Heartbeat
from bot.dashboard.server import start_dashboard, set_bot_manager
from bot.dashboard.bot_manager import BotManager
from bot.utils.logger import get_logger

log = get_logger(__name__)

# Railway injects PORT env var; fallback to DASHBOARD_PORT or 8080
DASHBOARD_PORT = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "8080")))


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1].strip()
    return value


def _normalize_profile_name(value: str) -> str:
    return _strip_quotes(value).strip().upper().replace("-", "_").replace(" ", "_")


def _get_env(key: str) -> str:
    return _strip_quotes(os.getenv(key, "") or "")


def _load_agent_profiles() -> list[dict]:
    raw = _strip_quotes(os.getenv("AGENT_PROFILES", "")).strip()
    if not raw:
        return [{
            "profile": "default",
            "api_key": _get_env("API_KEY"),
            "agent_private_key": _get_env("AGENT_PRIVATE_KEY"),
            "agent_wallet_address": _get_env("AGENT_WALLET_ADDRESS"),
            "agent_name": _get_env("AGENT_NAME"),
            "owner_eoa": _get_env("OWNER_EOA"),
        }]

    profiles = []
    for token in raw.split(","):
        if not token.strip():
            continue
        profile = _normalize_profile_name(token)
        api_key = _get_env(f"{profile}_API_KEY")
        agent_name = _get_env(f"{profile}_AGENT_NAME") or token.strip()
        if profile == "DEFAULT":
            api_key = api_key or _get_env("API_KEY")
            agent_name = agent_name or _get_env("AGENT_NAME")

        profiles.append({
            "profile": profile,
            "api_key": api_key,
            "agent_private_key": _get_env(f"{profile}_AGENT_PRIVATE_KEY"),
            "agent_wallet_address": _get_env(f"{profile}_AGENT_WALLET_ADDRESS"),
            "agent_name": agent_name,
            "owner_eoa": _get_env(f"{profile}_OWNER_EOA"),
            "room_mode": _get_env(f"{profile}_ROOM_MODE") or ("auto" if profile != "DEFAULT" else _get_env("ROOM_MODE") or "auto"),
        })

    if not profiles:
        log.warning("AGENT_PROFILES is set but no valid profiles were found. Falling back to default single agent.")
        return [{
            "profile": "default",
            "api_key": _get_env("API_KEY"),
            "agent_private_key": _get_env("AGENT_PRIVATE_KEY"),
            "agent_wallet_address": _get_env("AGENT_WALLET_ADDRESS"),
            "agent_name": _get_env("AGENT_NAME"),
            "owner_eoa": _get_env("OWNER_EOA"),
        }]

    return profiles


def main():
    """Entry point for the bot."""
    log.info("Molty Royale AI Agent v2.0.0")
    log.info("Press Ctrl+C to stop")

    profiles = _load_agent_profiles()
    manager = BotManager()
    set_bot_manager(manager)

    async def run_all():
        await start_dashboard(port=DASHBOARD_PORT)
        await manager.start_profiles(profiles)
        await asyncio.Event().wait()

    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(run_all())
    except KeyboardInterrupt:
        log.info("Shutdown complete.")
        try:
            asyncio.run(manager.stop_all())
        except Exception:
            pass


if __name__ == "__main__":
    main()
