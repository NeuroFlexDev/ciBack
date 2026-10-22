"""Independent operator service: uvicorn admin_main:app --port 8002."""
from app.application import create_app
app = create_app("admin")
