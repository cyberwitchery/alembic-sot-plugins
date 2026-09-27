from django.urls import include, path
from utilities.urls import get_model_urls

from . import views  # noqa: F401  (registers the model views)

urlpatterns = [
    path("backends/", include(get_model_urls("netbox_alembic", "backend", detail=False))),
    path("backends/<int:pk>/", include(get_model_urls("netbox_alembic", "backend"))),
    path("flows/", include(get_model_urls("netbox_alembic", "flow", detail=False))),
    path("flows/<int:pk>/", include(get_model_urls("netbox_alembic", "flow"))),
    path("runs/", include(get_model_urls("netbox_alembic", "run", detail=False))),
    path("runs/<int:pk>/", include(get_model_urls("netbox_alembic", "run"))),
]
