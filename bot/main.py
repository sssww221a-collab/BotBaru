"""
Molty Royale AI Agent — Entry Point v2.0.
Run: python -m bot.main
Dashboard + Bot run concurrently.
"""
import asyncio
import os
import sys
from bot.heartbeat import Heartbeat
from bot.dashboard.server import start_dashboard
from bot.utils.logger import get_logger

log = get_logger(__name__)

# Railway injects PORT env var; fallback to DASHBOARD_PORT or 8080
DASHBOARD_PORT = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "8080")))


def _normalize_profile_name(value: str) -> str:
    return value.strip().upper().replace("-", "_").replace(" ", "_")


def _load_agent_profiles() -> list[dict]:
    raw = os.getenv("AGENT_PROFILES", "").strip()
    if not raw:
        return [{
            "profile": "default",
            "api_key": os.getenv("API_KEY", ""),
            "agent_private_key": os.getenv("AGENT_PRIVATE_KEY", ""),
            "agent_wallet_address": os.getenv("AGENT_WALLET_ADDRESS", ""),
            "agent_name": os.getenv("AGENT_NAME", ""),
            "owner_eoa": os.getenv("OWNER_EOA", ""),
        }]

    profiles = []
    for token in raw.split(","):
        if not token.strip():
            continue
        profile = _normalize_profile_name(token)
        profiles.append({
            "profile": profile,
            "api_key": os.getenv(f"{profile}_API_KEY", ""),
            "agent_private_key": os.getenv(f"{profile}_AGENT_PRIVATE_KEY", ""),
            "agent_wallet_address": os.getenv(f"{profile}_AGENT_WALLET_ADDRESS", ""),
            "agent_name": os.getenv(f"{profile}_AGENT_NAME", token.strip()),
            "owner_eoa": os.getenv(f"{profile}_OWNER_EOA", ""),
        })

    if not profiles:
        log.warning("AGENT_PROFILES is set but no valid profiles were found. Falling back to default single agent.")
        return [{
            "profile": "default",
            "api_key": os.getenv("API_KEY", ""),
            "agent_private_key": os.getenv("AGENT_PRIVATE_KEY", ""),
            "agent_wallet_address": os.getenv("AGENT_WALLET_ADDRESS", ""),
            "agent_name": os.getenv("AGENT_NAME", ""),
            "owner_eoa": os.getenv("OWNER_EOA", ""),
        }]

    return profiles


def main():
    """Entry point for the bot."""
    log.info("Molty Royale AI Agent v2.0.0")
    log.info("Press Ctrl+C to stop")

    profiles = _load_agent_profiles()
    heartbeats = [Heartbeat(creds=profile, profile_name=profile["profile"]) for profile in profiles]

    async def run_all():
        await start_dashboard(port=DASHBOARD_PORT)
        await asyncio.gather(*(hb.run() for hb in heartbeats))

    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(run_all())
    except KeyboardInterrupt:
        log.info("Shutdown complete.")


if __name__ == "__main__":
    main()
