import os

# Central config defaults so modules can work even if env vars are not exported
HA_URL = os.environ.get("HA_URL", "http://localhost:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI3NmNmNGVkMjM1ODc0YzY5YWJkMzM3MmM3NzYyYmUxYyIsImlhdCI6MTc1Nzg3NjMzOSwiZXhwIjoyMDczMjM2MzM5fQ.kO4V01lih7PxmuC-CdJviZmavXRiAj_lXdHeC8jH5WQ")
