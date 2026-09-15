#!/usr/bin/env python3
"""Minimal Source-protocol RCON client for the Tristan-ValheimRcon plugin.

Why hand-rolled: the host has no rcon CLI and the plugin speaks plain Source
RCON (little-endian length, id, type, two NUL-terminated strings).  MEASURED
behaviour of ValheimRcon 1.6.2 that shapes this client:

  * auth is type 3, a successful auth answers with type 2 and the same id;
  * a command is type 2 and the answer is a single type-0 packet;
  * responses over ~4050 payload bytes are truncated by the plugin, and large
    responses have WEDGED this server twice by flooding the console log
    pipeline.  So every call site here keeps output small and this client
    refuses to reconnect in a tight loop.

Usage:
    python3 rcon.py "players"
    python3 rcon.py --file cmds.txt --quiet
"""

from __future__ import annotations

import argparse
import re
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

CONTAINER = "valheim-server-Ulfsland"
CONFIG = Path("/media/big4/projects/game/valheim/Ulfsland/config_merged/bepinex/org.tristan.rcon.cfg")

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


def container_ip(name: str = CONTAINER) -> str:
    """Resolve the bridge IP.  It CHANGES on every stop/start because hostops
    recreates the network, so it is resolved fresh rather than cached."""
    out = subprocess.run(
        ["sudo", "-n", "docker", "inspect", name, "--format",
         "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
        capture_output=True, text=True, check=True,
    )
    ip = out.stdout.strip()
    if not ip:
        raise RuntimeError(f"no bridge IP for {name}; is it running?")
    return ip


def rcon_credentials() -> tuple[int, str]:
    text = CONFIG.read_text()
    port = int(re.search(r"^Port\s*=\s*(\d+)", text, re.M).group(1))
    password = re.search(r"^Password\s*=\s*(\S+)", text, re.M).group(1)
    return port, password


class Rcon:
    def __init__(self, host: str | None = None, port: int | None = None,
                 password: str | None = None, timeout: float = 20.0):
        cfg_port, cfg_password = rcon_credentials()
        self.host = host or container_ip()
        self.port = port or cfg_port
        self.password = password or cfg_password
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._id = 0

    def __enter__(self) -> "Rcon":
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._send(SERVERDATA_AUTH, self.password)
        pkt_id, pkt_type, body = self._recv()
        if pkt_id == -1:
            raise RuntimeError("rcon auth rejected")
        if pkt_type not in (SERVERDATA_AUTH_RESPONSE, SERVERDATA_RESPONSE_VALUE):
            raise RuntimeError(f"unexpected auth response type {pkt_type}: {body!r}")

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _send(self, pkt_type: int, body: str) -> int:
        assert self.sock is not None
        self._id += 1
        payload = struct.pack("<ii", self._id, pkt_type) + body.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)
        return self._id

    def _read_exact(self, n: int) -> bytes:
        assert self.sock is not None
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("rcon connection closed mid-packet")
            buf += chunk
        return buf

    def _recv(self) -> tuple[int, int, str]:
        (length,) = struct.unpack("<i", self._read_exact(4))
        payload = self._read_exact(length)
        pkt_id, pkt_type = struct.unpack("<ii", payload[:8])
        body = payload[8:].rstrip(b"\x00").decode("utf-8", "replace")
        return pkt_id, pkt_type, body

    def command(self, cmd: str) -> str:
        if self.sock is None:
            self.connect()
        self._send(SERVERDATA_EXECCOMMAND, cmd)
        _, _, body = self._recv()
        return body

    def console(self, cmd: str) -> str:
        """Route through ValheimRcon's bridge to the server console, which is
        where Jere Kuusela's admin mods register their commands."""
        return self.command(f"consoleCommand {cmd}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="*")
    ap.add_argument("--file")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--max-print", type=int, default=600)
    args = ap.parse_args()

    cmds: list[str] = []
    if args.command:
        cmds.append(" ".join(args.command))
    if args.file:
        cmds += [ln.strip() for ln in Path(args.file).read_text().splitlines()
                 if ln.strip() and not ln.startswith("#")]
    if not cmds:
        ap.error("nothing to do")

    with Rcon() as rc:
        for cmd in cmds:
            reply = rc.command(cmd)
            if not args.quiet:
                shown = reply if len(reply) <= args.max_print else reply[:args.max_print] + f"... [{len(reply)} bytes]"
                print(f"> {cmd}\n{shown}")
            if args.delay:
                time.sleep(args.delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
