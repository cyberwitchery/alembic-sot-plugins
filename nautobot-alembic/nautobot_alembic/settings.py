"""app settings, and the alembic objects they resolve to."""

from alembic_runner import Backend as RunnerBackend
from alembic_runner import Flow as RunnerFlow
from alembic_runner import Runner, StateStore, plugin_names, token_env
from django.conf import settings


class SettingsError(RuntimeError):
    pass


def setting(name):
    from . import AlembicConfig

    configured = settings.PLUGINS_CONFIG.get("nautobot_alembic", {})
    return configured.get(name, AlembicConfig.default_settings[name])


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


def _token(backend):
    from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices

    try:
        return backend.secrets_group.get_secret_value(
            SecretsGroupAccessTypeChoices.TYPE_HTTP,
            SecretsGroupSecretTypeChoices.TYPE_TOKEN,
            obj=backend,
        )
    except Exception as e:  # SecretError, or a group without that secret
        raise SettingsError(
            f"no token for {backend} in secrets group {backend.secrets_group}: {e}"
        ) from e


def runner_backend(backend):
    """the alembic view of a stored backend, with its token from its secrets group.
    a plugin kind runs as that alembic plugin."""
    if backend.kind not in BUILTIN_KINDS:
        return RunnerBackend(backend.kind, plugin=True)
    env = token_env(backend.kind, _token(backend)) if backend.secrets_group_id else {}
    return RunnerBackend(backend.kind, dict(backend.config), env=env)


def runner_flow(flow):
    return RunnerFlow(
        id=str(flow.pk),
        target=runner_backend(flow.target),
        source=runner_backend(flow.source) if flow.source_id else None,
        allow_delete=flow.allow_delete,
        no_adopt=flow.no_adopt,
        state_key=flow.state_key or None,
    )
