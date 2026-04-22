"""FastAPI route modules carved out of ``demo_compare.py``.

Each submodule exposes a ``router`` (``fastapi.APIRouter``) that
``demo_compare.app`` mounts via ``app.include_router(router)``. Handlers
may reach back into demo_compare for vis/cache/backend helpers using
function-local imports to avoid a module-load cycle.
"""
