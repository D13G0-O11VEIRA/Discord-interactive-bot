"""Servidores falsos (status e RCON) usados nos testes, só com a biblioteca padrão."""

from __future__ import annotations

import asyncio
import json
import struct


# ---------------------------------------------------------------- status (SLP)
def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


async def _read_varint(reader: asyncio.StreamReader) -> int:
    result = 0
    for shift in range(0, 35, 7):
        byte = (await reader.readexactly(1))[0]
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result
    raise ValueError("varint")


class FakeStatusServer:
    def __init__(self, payload: dict, answer_ping: bool = True) -> None:
        self.payload = payload
        self.answer_ping = answer_ping
        self.server: asyncio.AbstractServer | None = None
        self.handshake_host: str | None = None

    async def __aenter__(self) -> "FakeStatusServer":
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc) -> None:
        self.server.close()
        await self.server.wait_closed()

    @property
    def port(self) -> int:
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer) -> None:
        try:
            await _read_varint(reader)  # tamanho do handshake
            assert await _read_varint(reader) == 0x00
            await _read_varint(reader)  # protocolo
            host_len = await _read_varint(reader)
            self.handshake_host = (await reader.readexactly(host_len)).decode()
            await reader.readexactly(2)  # porta
            assert await _read_varint(reader) == 1  # próximo estado = status
            await _read_varint(reader)  # tamanho do "status request"
            assert await _read_varint(reader) == 0x00

            body = json.dumps(self.payload).encode()
            inner = _varint(0x00) + _varint(len(body)) + body
            writer.write(_varint(len(inner)) + inner)
            await writer.drain()

            if self.answer_ping:
                await _read_varint(reader)
                assert await _read_varint(reader) == 0x01
                token = await reader.readexactly(8)
                pong = _varint(0x01) + token
                writer.write(_varint(len(pong)) + pong)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


# ---------------------------------------------------------------------- RCON
class FakeRconServer:
    """Imita o RCON do Minecraft, inclusive a resposta 'Unknown request 0' ao pacote sentinela."""

    def __init__(self, password: str, responses: dict[str, list[str]] | None = None) -> None:
        self.password = password
        self.responses = responses or {}
        self.received: list[str] = []
        self.connections = 0
        self.server: asyncio.AbstractServer | None = None

    async def __aenter__(self) -> "FakeRconServer":
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc) -> None:
        self.server.close()
        await self.server.wait_closed()

    @property
    def port(self) -> int:
        return self.server.sockets[0].getsockname()[1]

    @staticmethod
    def _pack(request_id: int, packet_type: int, body: str) -> bytes:
        data = struct.pack("<ii", request_id, packet_type) + body.encode() + b"\x00\x00"
        return struct.pack("<i", len(data)) + data

    async def _handle(self, reader, writer) -> None:
        self.connections += 1
        try:
            while True:
                (length,) = struct.unpack("<i", await reader.readexactly(4))
                data = await reader.readexactly(length)
                request_id, packet_type = struct.unpack("<ii", data[:8])
                body = data[8:-2].decode()
                if packet_type == 3:  # login
                    ok = body == self.password
                    writer.write(self._pack(request_id if ok else -1, 2, ""))
                elif packet_type == 2:  # comando
                    self.received.append(body)
                    for part in self.responses.get(body, ["ok"]):
                        writer.write(self._pack(request_id, 0, part))
                else:  # qualquer outro tipo (o pacote sentinela)
                    writer.write(self._pack(request_id, 0, "Unknown request 0"))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
