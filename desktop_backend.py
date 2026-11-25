"""Run the desktop-only loopback server for the lifetime of Electron's stdin."""
import logging
import os
from pathlib import Path
import sys
from threading import Thread

import uvicorn

from app import create_app

DATA_DIRECTORY_BUSY_EXIT = 73


class DesktopServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets)
        if self.started and not self.should_exit:
            # The model can redirect the standard streams during initialization.
            stdin = sys.stdin.fileno()
            Thread(target=self.stop_on_eof, args=(stdin,), daemon=True).start()
            address = self.servers[0].sockets[0].getsockname()
            print(f"AIRIMINI_READY {address[1]}", flush=True)

    def stop_on_eof(self, stdin):
        os.read(stdin, 1)
        self.should_exit = True


def main():
    if sys.argv[1:] == ["--check"]:
        from llama_cpp import llama_print_system_info

        print(llama_print_system_info().decode("utf-8"))
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    assets = Path(os.environ["AIRIMINI_ASSETS"])
    application = create_app(
        web_root=assets / "web",
        desktop_token=os.environ["AIRIMINI_DESKTOP_TOKEN"],
        data_examples=assets / "data.example",
    )
    server = DesktopServer(uvicorn.Config(
        application, host="127.0.0.1", port=0, http="h11", ws="none", loop="asyncio", lifespan="on",
        proxy_headers=False, access_log=False, log_config=None,
        timeout_graceful_shutdown=5,
    ))

    try:
        server.run()
    except SystemExit:
        if getattr(application.state, "data_directory_busy", False):
            raise SystemExit(DATA_DIRECTORY_BUSY_EXIT) from None
        raise
    if not server.started:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
