from nautobot.apps.api import OrderedDefaultRouter

from . import views

router = OrderedDefaultRouter()
router.register("backends", views.BackendViewSet)
router.register("flows", views.FlowViewSet)
router.register("runs", views.RunViewSet)

app_name = "nautobot_alembic-api"
urlpatterns = router.urls
