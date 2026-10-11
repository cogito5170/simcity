"""Minimal OpenTTD admin-port client (protocol from src/network/core/tcp_admin.h, OpenTTD 15.3).

Packet: uint16 size (little endian, includes this 3-byte header), uint8 type, payload.
Strings are UTF-8, NUL-terminated. Only what the mayor needs is implemented:
join (plain password, needs network.allow_insecure_admin_login), rcon, GameScript JSON both ways.
"""

from __future__ import annotations

import json
import socket
import struct
import time

ADMIN_JOIN, ADMIN_QUIT, ADMIN_UPDATE_FREQUENCY, ADMIN_POLL, ADMIN_CHAT, ADMIN_RCON, ADMIN_GAMESCRIPT, ADMIN_PING = range(8)
SERVER_FULL, SERVER_BANNED, SERVER_ERROR, SERVER_PROTOCOL, SERVER_WELCOME, SERVER_NEWGAME, SERVER_SHUTDOWN = range(100, 107)
SERVER_DATE = 107
SERVER_RCON = 120
SERVER_CONSOLE = 121
SERVER_GAMESCRIPT = 124
SERVER_RCON_END = 125
SERVER_PONG = 126
UPDATE_CONSOLE = 6
UPDATE_GAMESCRIPT = 9
FREQ_AUTOMATIC = 0x40


class AdminError(RuntimeError):
    pass


def _s(text: str) -> bytes:
    return text.encode("utf-8") + b"\0"


class Admin:
    def __init__(self, host: str = "127.0.0.1", port: int = 3977, password: str = "", name: str = "mayor",
                 timeout: float = 30.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""
        self.welcome: dict = {}
        self._send(ADMIN_JOIN, _s(password) + _s(name) + _s("1.0"))
        while not self.welcome:
            t, body = self._recv()
            if t == SERVER_WELCOME:
                self.welcome = self._parse_welcome(body)
            elif t in (SERVER_ERROR, SERVER_FULL, SERVER_BANNED):
                raise AdminError(f"server refused admin login (packet {t}, code {body[:1].hex()})")
        # GameScript JSON and console output arrive whenever they happen
        self._send(ADMIN_UPDATE_FREQUENCY, struct.pack("<HH", UPDATE_GAMESCRIPT, FREQ_AUTOMATIC))
        self._send(ADMIN_UPDATE_FREQUENCY, struct.pack("<HH", UPDATE_CONSOLE, FREQ_AUTOMATIC))

    # ---------------------------------------------------------------- wire --
    def _send(self, ptype: int, payload: bytes = b""):
        self.sock.sendall(struct.pack("<HB", len(payload) + 3, ptype) + payload)

    def _recv(self, timeout: float | None = None) -> tuple[int, bytes]:
        if timeout is not None:
            self.sock.settimeout(timeout)
        while len(self.buf) < 3 or len(self.buf) < struct.unpack_from("<H", self.buf)[0]:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise AdminError("server closed the admin connection")
            self.buf += chunk
        size, ptype = struct.unpack_from("<HB", self.buf)
        body, self.buf = self.buf[3:size], self.buf[size:]
        return ptype, body

    @staticmethod
    def _strings(body: bytes) -> list[str]:
        return [x.decode("utf-8", "replace") for x in body.split(b"\0")]

    def _parse_welcome(self, body: bytes) -> dict:
        parts = body.split(b"\0", 3)
        name, revision = parts[0].decode(), parts[1].decode()
        rest = parts[3] if len(parts) > 3 else b""
        # bool dedicated precedes the empty map-name string; after it: seed u32, landscape u8, start date u32, x u16, y u16
        rest = rest[1:] if rest[:1] == b"\0" else rest
        out = {"server": name, "revision": revision}
        if len(rest) >= 13:
            out["seed"], out["landscape"], out["start_date"], out["size_x"], out["size_y"] = struct.unpack_from("<IBIHH", rest)
        return out

    # ----------------------------------------------------------------- api --
    def rcon(self, command: str, timeout: float = 30.0) -> list[str]:
        """Run a console command on the server; returns the lines it printed."""
        self._send(ADMIN_RCON, _s(command))
        lines, end = [], time.time() + timeout
        while time.time() < end:
            t, body = self._recv(timeout=max(0.1, end - time.time()))
            if t == SERVER_RCON:
                lines.append(self._strings(body[2:])[0])
            elif t == SERVER_RCON_END:
                return lines
            elif t == SERVER_GAMESCRIPT:
                self.pending.append(json.loads(self._strings(body)[0]))
        raise AdminError(f"rcon timed out: {command}")

    pending: list = []

    def to_gs(self, data: dict):
        """Send a JSON object to the GameScript (it arrives there as a GSEventAdminPort)."""
        self._send(ADMIN_GAMESCRIPT, _s(json.dumps(data, separators=(",", ":"))))

    def from_gs(self, timeout: float = 30.0) -> dict | None:
        """Next JSON object the GameScript sent with GSAdmin.Send, or None on timeout."""
        if self.pending:
            return self.pending.pop(0)
        end = time.time() + timeout
        while time.time() < end:
            try:
                t, body = self._recv(timeout=max(0.1, end - time.time()))
            except (socket.timeout, TimeoutError):
                return None
            if t == SERVER_GAMESCRIPT:
                return json.loads(self._strings(body)[0])
        return None

    def close(self):
        try:
            self._send(ADMIN_QUIT)
        finally:
            self.sock.close()
