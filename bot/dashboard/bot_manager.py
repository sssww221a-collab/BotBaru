import asyncio
from typing import Any
from bot.heartbeat import Heartbeat
from bot.dashboard.state import dashboard_state
from bot.utils.logger import get_logger

log = get_logger(__name__)


class BotManager:
    """Manage Heartbeat instances for dashboard-controlled bot profiles."""

    def __init__(self):
        self._profiles: dict[str, dict[str, Any]] = {}
        self._heartbeats: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def normalize_profile(profile: str) -> str:
        return profile.strip() if profile else ""

    def _account_index(self, profile: str) -> int | None:
        for idx, acc in enumerate(dashboard_state.accounts):
            if acc.get("profile") == profile:
                return idx
        return None

    def _update_dashboard_account(self, profile: str, data: dict[str, Any]):
        idx = self._account_index(profile)
        if idx is None:
            dashboard_state.accounts.append({"profile": profile, **data})
        else:
            dashboard_state.accounts[idx].update(data)

    def add_or_update_profile(self, profile: str, account_data: dict[str, Any]):
        profile = self.normalize_profile(profile)
        if not profile:
            raise ValueError("Profile name is required")
        room_mode = (account_data.get("room_mode") or "auto").lower()
        if room_mode not in {"free", "paid", "auto"}:
            room_mode = "auto"

        profile_data = {**account_data, "profile": profile, "room_mode": room_mode}
        self._profiles[profile] = profile_data
        self._update_dashboard_account(profile, profile_data)
        status = "running" if profile in self._heartbeats else "stopped"
        self._update_dashboard_account(profile, {"status": status})

        if profile in self._heartbeats:
            heartbeat = self._heartbeats[profile]["heartbeat"]
            heartbeat.room_mode = room_mode
            heartbeat.creds["room_mode"] = room_mode
        log.info("Account profile saved: %s", profile)

    def get_profile(self, profile: str) -> dict[str, Any] | None:
        return self._profiles.get(profile) or next(
            (acc for acc in dashboard_state.accounts if acc.get("profile") == profile),
            None,
        )

    def list_profiles(self) -> list[dict[str, Any]]:
        accounts = []
        for acc in dashboard_state.accounts:
            profile = acc.get("profile")
            if not profile:
                continue
            account = {**acc, "status": self.get_status(profile)}
            accounts.append(account)
        return accounts

    def get_status(self, profile: str) -> str:
        return "running" if profile in self._heartbeats else "stopped"

    async def start_bot(self, profile: str):
        profile = self.normalize_profile(profile)
        async with self._lock:
            if profile in self._heartbeats:
                raise RuntimeError(f"Bot '{profile}' is already running")
            creds = self.get_profile(profile)
            if not creds or not creds.get("api_key"):
                raise KeyError(f"No account profile found for '{profile}' or API key missing")

            heartbeat = Heartbeat(creds=creds, profile_name=profile)
            task = asyncio.create_task(heartbeat.run())
            self._heartbeats[profile] = {"heartbeat": heartbeat, "task": task}
            self._update_dashboard_account(profile, {"status": "running"})
            dashboard_state.add_log(f"Bot profile started: {profile}", "info")
            log.info("Started bot profile: %s", profile)

    async def stop_bot(self, profile: str):
        profile = self.normalize_profile(profile)
        async with self._lock:
            entry = self._heartbeats.get(profile)
            if not entry:
                raise RuntimeError(f"Bot '{profile}' is not running")
            heartbeat = entry["heartbeat"]
            task = entry["task"]
            heartbeat.running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.warning("Error while stopping bot '%s': %s", profile, exc)
            self._heartbeats.pop(profile, None)
            self._update_dashboard_account(profile, {"status": "stopped"})
            dashboard_state.add_log(f"Bot profile stopped: {profile}", "info")
            log.info("Stopped bot profile: %s", profile)

    async def stop_all(self):
        async with self._lock:
            profiles = list(self._heartbeats.keys())
        for profile in profiles:
            try:
                await self.stop_bot(profile)
            except Exception as exc:
                log.warning("Failed to stop bot %s: %s", profile, exc)

    async def start_profiles(self, profiles: list[dict[str, Any]]):
        for profile_data in profiles:
            profile = self.normalize_profile(profile_data.get("profile") or profile_data.get("agent_name", ""))
            if not profile:
                continue
            if "room_mode" not in profile_data:
                profile_data["room_mode"] = "auto"
            self.add_or_update_profile(profile, profile_data)
            try:
                await self.start_bot(profile)
            except Exception as exc:
                log.warning("Could not auto-start profile %s: %s", profile, exc)

    def get_running_profiles(self) -> list[str]:
        return list(self._heartbeats.keys())
