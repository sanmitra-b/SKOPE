from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# API tests exercise authorization behavior with the documented local token.
# Keep them deterministic even when the developer's private .env enables
# Firebase for manual browser testing.
os.environ["AUTH_MODE"] = "dev"
