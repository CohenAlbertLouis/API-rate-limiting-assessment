"""Entry point: `uvicorn app.server:app`. Reads CONFIG_PATH and STORAGE from the environment."""
import logging
import os

from app.config import load_clients, store_from_env
from app.main import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

store = store_from_env(os.environ)
app = create_app(load_clients(os.environ.get("CONFIG_PATH", "config/clients.yaml")), store)
app.router.on_shutdown.append(store.close)
