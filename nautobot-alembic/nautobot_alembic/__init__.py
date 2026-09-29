from nautobot.apps import NautobotAppConfig, nautobot_database_ready

__version__ = "0.1.0"


class AlembicConfig(NautobotAppConfig):
    name = "nautobot_alembic"
    verbose_name = "Alembic"
    description = "Plan, review and apply alembic runs"
    version = __version__
    author = "cyberwitchery lab"
    author_email = "contact@cyberwitchery.com"
    base_url = "alembic"
    min_version = "3.2.0"
    max_version = "3.9999"
    default_settings = {
        "alembic_path": "alembic",
        "work_root": "/opt/alembic-work",
        "state": {"backend": "local"},
        "external_adapters": {},
        "require_distinct_approver": True,
        "plan_ttl_hours": 72,
        "run_timeout_seconds": 1800,
        "rust_log": None,
    }

    def ready(self):
        super().ready()
        from .signals import enable_jobs

        nautobot_database_ready.connect(enable_jobs, sender=self)


config = AlembicConfig
