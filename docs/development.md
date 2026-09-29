# development

```sh
dev/compose up -d --build --wait     # netbox 4.6.2 on :8011, with the plugin mounted
dev/manage test netbox_alembic       # plugin tests
dev/e2e.sh                           # plan, approve, apply, stale and drift, for real
NETBOX_IMAGE=docker.io/netboxcommunity/netbox:v4.7.1 NETBOX_PORT=8012 dev/compose up -d --build --wait
dev/nautobot-compose up -d --build --wait   # nautobot 3.2.5 on :8040, with the app mounted
dev/nautobot-manage test nautobot_alembic   # app tests
dev/nautobot-e2e.sh                         # the same end to end, plus an approval workflow
```

the core's tests run the real cli against a json-file adapter:

```sh
pip install -e "alembic-runner[dev]" alembic-adapter-sdk
cd alembic-runner && python -m unittest discover -s tests
```

## releasing

the three packages release together at one version, and the plugins pin
`alembic-runner` to it. bump `version` in all three `pyproject.toml` files and
both pins, then push a `v<version>` tag: the publish workflow checks that
everything matches the tag, builds, and publishes to pypi.
