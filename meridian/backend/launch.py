"""Open the service port before the application loads, then hand it to uvicorn.

On App Runner the backend needs close to thirty seconds on one vCPU to import
its dependencies and initialise the Aurora checkpoint backend, and a service
whose port stayed closed that long failed to deploy every time. This launcher
binds the port in milliseconds and passes the listening socket to uvicorn with
``--fd``; connections that arrive before the application is ready wait in the
kernel backlog and are served once startup completes.
"""

from __future__ import annotations

import os
import socket
import sys

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8000"))


def main() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((HOST, PORT))
    listener.listen(128)
    listener.set_inheritable(True)
    os.execv(
        sys.executable,
        [
            sys.executable, "-m", "uvicorn", "backend.main:app",
            "--fd", str(listener.fileno()),
            "--proxy-headers", "--forwarded-allow-ips", "*",
            "--timeout-keep-alive", "75",
        ],
    )


if __name__ == "__main__":
    main()
