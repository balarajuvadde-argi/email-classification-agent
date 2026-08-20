from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from email_classification_agent.handler import lambda_handler  # noqa: E402,F401
