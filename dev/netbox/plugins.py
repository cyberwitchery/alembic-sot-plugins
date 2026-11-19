PLUGINS = ["netbox_alembic"]
PLUGINS_CONFIG = {
    "netbox_alembic": {
        "work_root": "/opt/alembic-work",
        "credentials": {
            "netbox": {"token": "ALEMBIC_NETBOX_TOKEN", "url": "http://netbox:8080"},
        },
        # dev/alembic-plugins: each plugin in it is a backend kind.
        "plugins_dir": "/opt/alembic-plugins",
    },
}

# dev only: lets manage.py makemigrations run.
DEVELOPER = True
