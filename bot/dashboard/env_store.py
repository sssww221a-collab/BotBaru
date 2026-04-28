import os
from pathlib import Path
from typing import Any
from dotenv import load_dotenv, set_key, unset_key

if os.path.exists("/app/data"):
    ENV_FILE = Path("/app/data") / ".env"
else:
    ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _ensure_env_file() -> Path:
    if not ENV_FILE.exists():
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        ENV_FILE.write_text("")
    return ENV_FILE


def _load_dotenv_file() -> None:
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=False)
    else:
        load_dotenv()


def _set_env_value(key: str, value: str | None) -> None:
    env_path = _ensure_env_file()
    if value is None or value == "":
        unset_key(str(env_path), key)
    else:
        set_key(str(env_path), key, str(value))


def persist_agent_profile(profile: str, account_data: dict[str, Any]) -> None:
    """Persist a dashboard account profile into the root .env file."""
    profile = profile.strip()
    if not profile:
        raise ValueError("Profile name is required")

    _load_dotenv_file()
    profile_key = profile.upper()
    room_mode = (account_data.get("room_mode") or "auto").lower()
    if profile_key == "DEFAULT":
        _set_env_value("API_KEY", account_data.get("api_key", ""))
        _set_env_value("AGENT_PRIVATE_KEY", account_data.get("agent_private_key", ""))
        _set_env_value("AGENT_WALLET_ADDRESS", account_data.get("agent_wallet_address", ""))
        _set_env_value("AGENT_NAME", account_data.get("agent_name", ""))
        _set_env_value("OWNER_EOA", account_data.get("owner_eoa", ""))
        _set_env_value("ROOM_MODE", room_mode)
    else:
        _set_env_value(f"{profile_key}_API_KEY", account_data.get("api_key", ""))
        _set_env_value(f"{profile_key}_AGENT_PRIVATE_KEY", account_data.get("agent_private_key", ""))
        _set_env_value(f"{profile_key}_AGENT_WALLET_ADDRESS", account_data.get("agent_wallet_address", ""))
        _set_env_value(f"{profile_key}_AGENT_NAME", account_data.get("agent_name", ""))
        _set_env_value(f"{profile_key}_OWNER_EOA", account_data.get("owner_eoa", ""))
        _set_env_value(f"{profile_key}_ROOM_MODE", room_mode)

    existing_profiles = os.getenv("AGENT_PROFILES", "")
    profile_list = [p.strip() for p in existing_profiles.split(",") if p.strip()]
    normalized = [p.upper() for p in profile_list]
    if profile_key not in normalized:
        profile_list.append(profile_key)
        _set_env_value("AGENT_PROFILES", ",".join(profile_list))
