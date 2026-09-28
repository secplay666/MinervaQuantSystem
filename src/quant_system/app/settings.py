"""API settings (ADR-010 §4): read from an env file outside the repository
(default ``~/.config/minerva/app.env``) and overridable by environment
variables.  The signing secret never lives in the repository."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from .db.base import DEFAULT_DB
from .notify import NotifyConfig

DEFAULT_ENV_FILE = Path.home() / ".config" / "minerva" / "app.env"
ENV_LABELS = {"test": "测试环境", "production": "生产环境", "development": "开发环境"}


class SettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppSettings:
    root: Path
    db_path: Path
    secret_key: str
    environment: str = "test"
    access_minutes: int = 15
    refresh_days: int = 7
    lockout_threshold: int = 5
    lockout_minutes: int = 15
    min_password_length: int = 10
    cors_origins: tuple[str, ...] = ()
    # Peers whose X-Forwarded-For is believed.  None by default: behind frp's TCP forward every
    # connection comes from 127.0.0.1 and nobody sets the header, so any client could choose its IP.
    trusted_proxies: tuple[str, ...] = ()
    web_dir: Path | None = None  # built PC frontend served at /, if present
    mobile_dir: Path | None = None  # built mobile frontend served at /m/ (phones without the app)
    notify: NotifyConfig = field(default_factory=NotifyConfig)  # external channels (app/notify.py)
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def environment_label(self) -> str:
        return ENV_LABELS.get(self.environment, self.environment)

    @property
    def market_db(self) -> Path:
        return self.root / "data" / "market.duckdb"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _private_dir(directory: Path) -> None:
    try:
        directory.chmod(0o700)
    except OSError:  # Windows: best effort
        pass


def init_env_file(path: Path = DEFAULT_ENV_FILE) -> bool:
    """Create the env file with a fresh secret (mode 600); False if it exists."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    _private_dir(path.parent)
    path.write_text(f"MINERVA_SECRET_KEY={secrets.token_urlsafe(48)}\nMINERVA_ENV=test\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # Windows: best effort
        pass
    return True


def load_settings(root: Path, env_file: Path | None = None, require_secret: bool = True,
                  **overrides: object) -> AppSettings:
    """``require_secret=False`` for commands that never sign tokens (events, notifications)."""
    values = read_env_file(env_file or Path(os.environ.get("MINERVA_ENV_FILE", DEFAULT_ENV_FILE)))
    values.update({k: v for k, v in os.environ.items() if k.startswith("MINERVA_")})
    secret = str(overrides.pop("secret_key", None) or values.get("MINERVA_SECRET_KEY", ""))
    if len(secret) < 32 and require_secret:
        raise SettingsError("MINERVA_SECRET_KEY is missing or too short; run `quant-app init-secret`")
    db = overrides.pop("db_path", None) or values.get("MINERVA_DB")
    web = overrides.pop("web_dir", None) or values.get("MINERVA_WEB_DIR")
    mobile = overrides.pop("mobile_dir", None) or values.get("MINERVA_MOBILE_DIR")
    cors = values.get("MINERVA_CORS", "")
    return AppSettings(
        root=root, db_path=Path(db) if db else root / DEFAULT_DB, secret_key=secret,
        environment=str(overrides.pop("environment", None) or values.get("MINERVA_ENV", "test")),
        cors_origins=tuple(o.strip() for o in cors.split(",") if o.strip()),
        web_dir=Path(web) if web else None, mobile_dir=Path(mobile) if mobile else None,
        notify=overrides.pop("notify", None) or NotifyConfig.from_values(values), **overrides)  # type: ignore[arg-type]
