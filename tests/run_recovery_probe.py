"""Run the recovery test against fresh, isolated Compose data stores."""
import os
import runpy
import select
import socket
import socketserver
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import httpx

from app.store import PostgresStore


class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        with socket.create_connection(self.server.target, timeout=15) as upstream:
            peers = {self.request: upstream, upstream: self.request}
            while incoming := select.select(peers, (), (), 180)[0]:
                for sender in incoming:
                    data = sender.recv(65536)
                    if not data:
                        return
                    peers[sender].sendall(data)


class RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if os.getenv("SAATHI_RECOVERY_PROBE") != "1":
    raise SystemExit("Use the isolated Compose override in the README.")

services = {
    "app": "http://127.0.0.1:8000/api/health",
    "identity": "http://identity:8080/realms/saathi/.well-known/openid-configuration",
    "OCR": "http://ocr:8010/openapi.json",
}
deadline = monotonic() + 90
with httpx.Client(timeout=2, trust_env=False) as readiness:
    for name, url in services.items():
        while True:
            try:
                if readiness.get(url).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if monotonic() >= deadline:
                raise SystemExit(f"{name} was not ready for the recovery probe.")
            sleep(1)

with PostgresStore(os.environ["DATABASE_URL"])._connect() as conn:
    if conn.execute("SELECT EXISTS (SELECT 1 FROM owner_cases)").fetchone()[0]:
        raise SystemExit("The recovery probe requires a fresh case store.")

# Keycloak redirects still use the sandbox loopback origin. Relay it inside this
# container so the test needs no public or host port bindings.
for port, target in ((8174, ("127.0.0.1", 8000)), (8175, ("identity", 8080))):
    relay = RelayServer(("127.0.0.1", port), Relay)
    relay.target = target
    Thread(target=relay.serve_forever, daemon=True).start()

runpy.run_path(str(Path(__file__).with_name("recovery_probe.py")), run_name="__main__")
