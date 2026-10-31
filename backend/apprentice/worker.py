"""A standalone job worker, for hosting: run as many as you need next to the web servers.

    python -m apprentice.worker      (from the backend folder, with the same .env / environment as the web app)
"""

from __future__ import annotations

import asyncio
import logging
import signal

from . import mock
from .config import get_settings
from .jobs import Worker
from .muse import MuseClient
from .store import Store
from .trace import Tracer


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = get_settings()
    store = Store(settings.data_dir, settings.database_url)
    tracer = Tracer(settings.trace_dir)
    muse = MuseClient(settings, tracer, mock_responder=mock.respond)
    worker = Worker(store, muse, settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop)
        except NotImplementedError:  # Windows
            pass
    logging.getLogger("showme.worker").info("worker %s started (%s)", worker.name,
                                            "Postgres" if store.postgres else "SQLite")
    try:
        await worker.run_forever()
    finally:
        await muse.aclose()
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
