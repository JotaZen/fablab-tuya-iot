import os

# Central config defaults so modules can work even if env vars are not exported
HA_URL = os.environ.get("HA_URL", "http://localhost:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjZmIxZDYyNmVlODc0MjY2OTJhYjMwZmUxYmI0YTBhMiIsImlhdCI6MTc1NTY0MjU4OSwiZXhwIjoyMDcxMDAyNTg5fQ.v7yQXhrD41Xuba57UMRmcLtGtO6fSfEZLUT1QQ0kPN4")
