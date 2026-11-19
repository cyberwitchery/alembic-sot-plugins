"""plugin settings, and the alembic objects they resolve to."""

import os

from alembic_runner import Backend as RunnerBackend
from alembic_runner import Flow as RunnerFlow
from alembic_runner import Runner, StateStore, plugin_names, token_env
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
        plugins_dir=setting("plugins_dir"),
    )


# the backends alembic has built in, as the ui names them. every other kind is
# an alembic plugin in plugins_dir.
BUILTIN_KINDS = {
    "netbox": "NetBox",
    "nautobot": "Nautobot",
    "infrahub": "Infrahub",
    "peeringdb": "PeeringDB",
}


def kind_choices():
    """(kind, label) for every kind a backend can have: the built-in ones, then
    the alembic plugins in plugins_dir."""
    plugins = [(name, name) for name in plugin_names(setting("plugins_dir"))]
    return [*BUILTIN_KINDS.items(), *(p for p in plugins if p[0] not in BUILTIN_KINDS)]


def _worker_env(name):
    try:
        return os.environ[name]
    except KeyError:
        raise SettingsError(f"worker environment variable {name} is not set") from None


def _credential(name):
    credentials = setting("credentials")
    if name not in credentials:
        raise SettingsError(f"no credential {name!r} in plugin settings")
    return credentials[name]


def _credential_env(kind, name):
    entry = _credential(name)
    env = {}
    if "token" in entry:
        if kind not in BUILTIN_KINDS:
            raise SettingsError(
                f"credential {name!r} has a token; a plugin reads its variables, set them with env"
            )
        env.update(token_env(kind, _worker_env(entry["token"])))
    for adapter_var, worker_var in entry.get("env", {}).items():
        env[adapter_var] = _worker_env(worker_var)
    return env


def connection_config(kind, config, credential):
    """the config a built-in backend connects with. a credential goes only to the
    url plugin settings name for it: a stored url may repeat that url, never
    replace it, so whoever can edit a backend cannot send a credential elsewhere."""
    config = dict(config)
    if not credential:
        return config
    url = _credential(credential).get("url")
    if not url:
        raise SettingsError(f"credential {credential!r} names no url in plugin settings")
    if config.setdefault("url", url) != url:
        raise SettingsError(
            f"credential {credential!r} is for {url}, not {config['url']}; "
            "leave the url empty or set it to that"
        )
    return config


def runner_backend(backend):
    """the alembic view of a stored backend, with credentials from the worker
    environment. a plugin kind runs as that alembic plugin."""
    name = backend.credential
    if backend.kind not in BUILTIN_KINDS:
        env = _credential_env(backend.kind, name) if name else {}
        return RunnerBackend(backend.kind, env=env, plugin=True)
    config = connection_config(backend.kind, backend.config, name)
    env = _credential_env(backend.kind, name) if name else {}
    return RunnerBackend(backend.kind, config, env=env)


def runner_flow(flow):
    return RunnerFlow(
        id=str(flow.pk),
        target=runner_backend(flow.target),
        source=runner_backend(flow.source) if flow.source_id else None,
        allow_delete=flow.allow_delete,
        no_adopt=flow.no_adopt,
        state_key=flow.state_key or None,
    )
