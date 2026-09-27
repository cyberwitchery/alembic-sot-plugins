from netbox.plugins import PluginMenuButton, PluginMenuItem

menu_items = (
    PluginMenuItem(
        link="plugins:netbox_alembic:flow_list",
        link_text="Flows",
        permissions=["netbox_alembic.view_flow"],
        buttons=(
            PluginMenuButton(
                "plugins:netbox_alembic:flow_add",
                "Add",
                "mdi mdi-plus-thick",
                permissions=["netbox_alembic.add_flow"],
            ),
        ),
    ),
    PluginMenuItem(
        link="plugins:netbox_alembic:run_list",
        link_text="Runs",
        permissions=["netbox_alembic.view_run"],
    ),
    PluginMenuItem(
        link="plugins:netbox_alembic:backend_list",
        link_text="Backends",
        permissions=["netbox_alembic.view_backend"],
        buttons=(
            PluginMenuButton(
                "plugins:netbox_alembic:backend_add",
                "Add",
                "mdi mdi-plus-thick",
                permissions=["netbox_alembic.add_backend"],
            ),
        ),
    ),
)
