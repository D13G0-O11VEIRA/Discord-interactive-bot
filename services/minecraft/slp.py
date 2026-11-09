"""Consulta de status de servidores Java (Server List Ping), sem dependências externas.

É o mesmo protocolo que o jogo usa para montar a lista de servidores:
funciona com qualquer servidor Java online (inclusive Aternos) e não exige
RCON, plugin nem acesso aos arquivos.
"""

from __future__ import annotations

import asyncio
import json
import re
import struct
import time
from dataclasses import dataclass, field

# O protocolo da versão não importa para o status; 47 (1.8) é o valor que o mcstatus usa.
_HANDSHAKE_PROTOCOL = 47
_MAX_RESPONSE = 4 * 1024 * 1024
_FORMAT_RE = re.compile(r"§.")


def strip_formatting(text: str) -> str:
    """Remove os códigos de cor/formatação ('§a', '§l', ...)."""
    return _FORMAT_RE.sub("", text)


@dataclass
class ServerStatus:
    version_name: str
    protocol: int
    players_online: int
    players_max: int
    player_names: list[str]
    motd: str
    latency_ms: int | None
    raw: dict = field(repr=False, default_factory=dict)


def _varint(value: int) -> bytes:
    value &= 0xFFFFFFFF
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
            return result - (1 << 32) if result >= (1 << 31) else result
    raise ValueError("VarInt grande demais")


def _packet(packet_id: int, payload: bytes = b"") -> bytes:
    body = _varint(packet_id) + payload
    return _varint(len(body)) + body


def _string(value: str) -> bytes:
    data = value.encode("utf-8")
    return _varint(len(data)) + data


def _flatten_description(desc) -> str:
    if isinstance(desc, str):
        return desc
    if isinstance(desc, list):
        return "".join(_flatten_description(item) for item in desc)
    if isinstance(desc, dict):
        text = desc.get("text", "")
        text = text if isinstance(text, str) else ""
        return text + "".join(_flatten_description(extra) for extra in desc.get("extra", []))
    return ""


def _parse(raw: dict, latency_ms: int | None) -> ServerStatus:
    version = raw.get("version") or {}
    players = raw.get("players") or {}
    sample = players.get("sample") or []
    return ServerStatus(
        version_name=strip_formatting(str(version.get("name", ""))),
        protocol=int(version.get("protocol", 0) or 0),
        players_online=int(players.get("online", 0) or 0),
        players_max=int(players.get("max", 0) or 0),
        player_names=[str(p.get("name", "")) for p in sample if isinstance(p, dict) and p.get("name")],
        motd=strip_formatting(_flatten_description(raw.get("description", ""))).strip(),
        latency_ms=latency_ms,
        raw=raw,
    )


async def _ping(host: str, port: int) -> ServerStatus:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        handshake = _varint(_HANDSHAKE_PROTOCOL) + _string(host) + struct.pack(">H", port) + _varint(1)
        writer.write(_packet(0x00, handshake) + _packet(0x00))
        await writer.drain()

        await _read_varint(reader)  # tamanho do pacote
        packet_id = await _read_varint(reader)
        if packet_id != 0x00:
            raise ValueError(f"resposta inesperada do servidor (pacote {packet_id})")
        length = await _read_varint(reader)
        if not 0 < length <= _MAX_RESPONSE:
            raise ValueError("resposta de status com tamanho inválido")
        raw = json.loads((await reader.readexactly(length)).decode("utf-8"))

        latency = None
        try:
            started = time.perf_counter()
            writer.write(_packet(0x01, struct.pack(">q", int(started * 1000))))
            await writer.drain()
            await _read_varint(reader)
            await _read_varint(reader)
            await reader.readexactly(8)
            latency = round((time.perf_counter() - started) * 1000)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass  # alguns proxies não respondem ao ping; o status já foi lido
        return _parse(raw, latency)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 - encerrar a conexão nunca deve gerar erro
            pass


async def ping(host: str, port: int = 25565, timeout: float = 5.0) -> ServerStatus:
    """Consulta o servidor. Levanta exceção (OSError, TimeoutError, ValueError...) se falhar."""
    return await asyncio.wait_for(_ping(host, port), timeout)
