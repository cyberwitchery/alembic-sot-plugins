"""git repositories can say they hold alembic inventories. a flow reads its
repository at plan time, so a sync has nothing to load."""

from nautobot.apps.datasources import DatasourceContent


def refresh_inventories(repository_record, job_result, delete=False):
    """nothing to load: a flow snapshots its files when it plans."""


datasource_contents = [
    (
        "extras.gitrepository",
        DatasourceContent(
            name="alembic inventories",
            content_identifier="nautobot_alembic.inventories",
            icon="mdi-source-branch",
            callback=refresh_inventories,
        ),
    )
]
