"""app settings, and the alembic objects they resolve to."""

from alembic_runner import Backend as RunnerBackend
from alembic_runner import Flow as RunnerFlow
from alembic_runner import Runner, StateStore, token_env
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
    )


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
    """the alembic view of a stored backend, with its token from its secrets group."""
    env = {}
    command = None
    if backend.kind == "external":
        adapter = setting("external_adapters").get(backend.external_adapter)
        if adapter is None:
            raise SettingsError(f"no external adapter {backend.external_adapter!r} in settings")
        # an adapter is a path, or {"command": path, "token_env": NAME} for one
        # that reads its token from a variable of its own.
        if isinstance(adapter, str):
            adapter = {"command": adapter}
        command = adapter["command"]
        if backend.secrets_group_id and adapter.get("token_env"):
            env[adapter["token_env"]] = _token(backend)
    elif backend.secrets_group_id:
        env = token_env(backend.kind, _token(backend))
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
