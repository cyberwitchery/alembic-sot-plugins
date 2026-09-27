PLUGINS = ["netbox_alembic"]
PLUGINS_CONFIG = {
    "netbox_alembic": {
        "work_root": "/opt/alembic-work",
        "self_url": "http://netbox:8080",
        "self_credential": "self",
        "credentials": {"self": {"token": "ALEMBIC_SELF_TOKEN"}},
    },
}

# dev only: lets manage.py makemigrations run.
DEVELOPER = True
