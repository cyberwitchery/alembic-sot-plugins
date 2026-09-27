"""backends and state stores, as the environment and files a run hands to alembic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KINDS = ("netbox", "nautobot", "infrahub", "peeringdb", "external")

# the config keys a stored backend may set. anything that names a command, a
# working directory or an environment for a process stays in host settings:
# whoever can edit a backend must not be able to run something on the worker.
CONFIG_KEYS = {
    "netbox": frozenset({"url", "instance"}),
    "nautobot": frozenset({"url", "instance"}),
    "infrahub": frozenset({"url", "instance"}),
    "peeringdb": frozenset({"url", "instance"}),
    "external": frozenset({"args", "setup", "timeout_seconds", "instance"}),
}

# the environment variable each built-in adapter reads its credential from.
TOKEN_ENV = {
    "netbox": "NETBOX_TOKEN",
    "nautobot": "NAUTOBOT_TOKEN",
    "infrahub": "INFRAHUB_TOKEN",
    "peeringdb": "PEERINGDB_API_KEY",
}


class BackendError(ValueError):
    pass


@dataclass(frozen=True)
class Backend:
    """one backend as a run sees it.

    `config` is the non-secret part of an alembic backend config, without the
    `backend:` key. `env` holds the credentials, by variable name. an external
    backend's `command` comes from host settings, never from stored config.
    """

    kind: str
    config: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    command: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise BackendError(f"unknown backend kind {self.kind!r}")
        unknown = self.config.keys() - CONFIG_KEYS[self.kind]
        if unknown:
            raise BackendError(
                f"a {self.kind} backend config cannot set {', '.join(sorted(unknown))}; "
                f"it takes {', '.join(sorted(CONFIG_KEYS[self.kind]))}"
            )
        if self.kind == "external":
            if not self.command:
                raise BackendError("an external backend needs a command from host settings")
        elif self.command:
            raise BackendError(f"a {self.kind} backend takes no command")

    def config_document(self) -> dict[str, Any]:
        doc = {"backend": self.kind, **self.config}
        if self.command:
            doc["command"] = self.command
        return doc


def token_env(kind: str, token: str) -> dict[str, str]:
    """the environment that hands `token` to a built-in adapter of `kind`."""
    try:
        return {TOKEN_ENV[kind]: token}
    except KeyError:
        raise BackendError(f"a {kind} backend reads no token variable") from None


@dataclass(frozen=True)
class StateStore:
    """where identity state lives. `local` keeps it in the flow directory."""

    backend: str = "local"
    postgres_url: str | None = None
    postgres_tls: str | None = None

    def __post_init__(self) -> None:
        if self.backend not in ("local", "postgres"):
            raise BackendError(f"unknown state backend {self.backend!r}")
        if self.backend == "postgres" and not self.postgres_url:
            raise BackendError("postgres state needs a postgres_url")

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None) -> StateStore:
        settings = settings or {}
        return cls(
            backend=settings.get("backend", "local"),
            postgres_url=settings.get("postgres_url"),
            postgres_tls=settings.get("postgres_tls"),
        )

    def env(self, key: str) -> dict[str, str]:
        if self.backend == "local":
            return {"ALEMBIC_STATE_BACKEND": "local"}
        env = {
            "ALEMBIC_STATE_BACKEND": "postgres",
            "ALEMBIC_STATE_POSTGRES_URL": self.postgres_url or "",
            "ALEMBIC_STATE_KEY": key,
        }
        if self.postgres_tls:
            env["ALEMBIC_STATE_POSTGRES_TLS"] = self.postgres_tls
        return env
