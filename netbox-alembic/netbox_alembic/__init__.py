from netbox.plugins import PluginConfig

__version__ = "0.1.0"


class AlembicConfig(PluginConfig):
    name = "netbox_alembic"
    verbose_name = "Alembic"
    description = "Plan, review and apply alembic runs"
    version = __version__
    author = "cyberwitchery lab"
    author_email = "contact@cyberwitchery.com"
    base_url = "alembic"
    min_version = "4.6.0"
    queues = ["alembic"]
    default_settings = {
        "alembic_path": "alembic",
        "work_root": "/opt/alembic-work",
        "state": {"backend": "local"},
        "credentials": {},
        "external_adapters": {},
        "require_distinct_approver": True,
        "plan_ttl_hours": 72,
        "run_timeout_seconds": 1800,
        "rust_log": None,
    }


config = AlembicConfig
