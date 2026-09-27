from netbox.api.routers import NetBoxRouter

from . import views

app_name = "netbox_alembic-api"

router = NetBoxRouter()
router.register("backends", views.BackendViewSet)
router.register("flows", views.FlowViewSet)
router.register("runs", views.RunViewSet)

urlpatterns = router.urls
