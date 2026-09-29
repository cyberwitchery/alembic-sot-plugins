"""plugin settings, and the alembic objects they resolve to."""

import os

from alembic_runner import Backend as RunnerBackend
from alembic_runner import Flow as RunnerFlow
from alembic_runner import Runner, StateStore, token_env
from netbox.plugins import get_plugin_config


class SettingsError(RuntimeError):
    pass


def setting(name):
    return get_plugin_config("netbox_alembic", name)


def runner():
    return Runner(
        workspace_root=setting("work_root"),
        state=StateStore.from_settings(setting("state")),
        alembic_path=setting("alembic_path"),
        timeout=setting("run_timeout_seconds"),
        rust_log=setting("rust_log"),
    )


def _worker_env(name):
    try:
        return os.environ[name]
    except KeyError:
        raise SettingsError(f"worker environment variable {name} is not set") from None


def _credential_env(kind, name):
    credentials = setting("credentials")
    if name not in credentials:
        raise SettingsError(f"no credential {name!r} in plugin settings")
    entry = credentials[name]
    env = {}
    if "token" in entry:
        env.update(token_env(kind, _worker_env(entry["token"])))
    for adapter_var, worker_var in entry.get("env", {}).items():
        env[adapter_var] = _worker_env(worker_var)
    return env


def runner_backend(backend):
    """the alembic view of a stored backend, with credentials from the worker environment."""
    env = _credential_env(backend.kind, backend.credential) if backend.credential else {}
    command = None
    if backend.kind == "external":
        adapters = setting("external_adapters")
        if backend.external_adapter not in adapters:
            raise SettingsError(f"no external adapter {backend.external_adapter!r} in settings")
        command = adapters[backend.external_adapter]
    return RunnerBackend(backend.kind, dict(backend.config), env=env, command=command)


def runner_flow(flow):
    return RunnerFlow(
        id=str(flow.pk),
        target=runner_backend(flow.target),
        source=runner_backend(flow.source) if flow.source_id else None,
        allow_delete=flow.allow_delete,
        no_adopt=flow.no_adopt,
        state_key=flow.state_key or None,
    )
