"""Public platform API. Operator routes are served only by admin_main."""
from app.application import create_app
app = create_app("platform")
