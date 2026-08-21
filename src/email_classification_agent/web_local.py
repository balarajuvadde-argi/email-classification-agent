from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet
from dotenv import load_dotenv


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    load_dotenv(".env.web", override=False)
    load_dotenv(".env", override=False)
    os.environ.setdefault("APP_ENV", "development")
    os.environ.setdefault("APP_BASE_URL", "http://localhost:8000")
    if os.environ["APP_ENV"].casefold() == "development":
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    os.environ.setdefault(
        "LOCAL_TOKEN_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    import uvicorn

    from .web_app import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=8000, access_log=True, log_level="info")


if __name__ == "__main__":
    main()
