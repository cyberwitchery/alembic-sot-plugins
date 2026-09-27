PLUGINS = ["netbox_alembic"]
PLUGINS_CONFIG = {
    "netbox_alembic": {
        "work_root": "/opt/alembic-work",
        "credentials": {"netbox": {"token": "ALEMBIC_NETBOX_TOKEN"}},
    },
}

# dev only: lets manage.py makemigrations run.
DEVELOPER = True
