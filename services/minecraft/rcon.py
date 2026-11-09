"""Cliente RCON assíncrono (protocolo Source RCON usado pelo Minecraft)."""

from __future__ import annotations

import asyncio
import struct

_TYPE_RESPONSE = 0
_TYPE_COMMAND = 2
_TYPE_LOGIN = 3
_MAX_PACKET = 8192


class RconError(Exception):
    """Falha de conexão ou de protocolo no RCON."""


class RconAuthError(RconError):
    """Senha do RCON recusada."""


class RconClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._next_id = 1

    # --- baixo nível -------------------------------------------------------
    async def _send(self, request_id: int, packet_type: int, body: str) -> None:
        assert self._writer is not None
        data = struct.pack("<ii", request_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
        self._writer.write(struct.pack("<i", len(data)) + data)
        await self._writer.drain()

    async def _recv(self) -> tuple[int, int, str]:
        assert self._reader is not None
        (length,) = struct.unpack("<i", await self._reader.readexactly(4))
        if not 10 <= length <= _MAX_PACKET:
            raise RconError(f"pacote RCON com tamanho inválido ({length})")
        data = await self._reader.readexactly(length)
        request_id, packet_type = struct.unpack("<ii", data[:8])
        return request_id, packet_type, data[8:-2].decode("utf-8", errors="replace")

    async def _connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout
        )
        login_id = self._next_id
        self._next_id += 1
        await self._send(login_id, _TYPE_LOGIN, self.password)
        while True:  # alguns servidores mandam um pacote vazio antes da resposta de login
            request_id, packet_type, _ = await asyncio.wait_for(self._recv(), self.timeout)
            if packet_type == _TYPE_COMMAND:  # tipo 2 = resposta de autenticação
                break
        if request_id == -1:
            await self._close_quiet()
            raise RconAuthError("senha do RCON incorreta")

    async def _close_quiet(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _run(self, command: str) -> str:
        request_id = self._next_id
        self._next_id += 2
        await self._send(request_id, _TYPE_COMMAND, command)
        # Pacote "sentinela": o servidor responde a ele depois da resposta completa,
        # o que permite juntar respostas grandes que vêm fragmentadas.
        await self._send(request_id + 1, _TYPE_RESPONSE, "")
        parts: list[str] = []
        while True:
            rid, _, body = await self._recv()
            if rid == request_id + 1:
                return "".join(parts)
            if rid == request_id:
                parts.append(body)

    # --- API pública -------------------------------------------------------
    async def command(self, command: str) -> str:
        """Executa um comando no console e devolve a resposta (reconecta sozinho 1 vez)."""
        async with self._lock:
            for attempt in (1, 2):
                try:
                    if self._writer is None:
                        await self._connect()
                    return await asyncio.wait_for(self._run(command), self.timeout)
                except RconAuthError:
                    raise
                except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError, RconError) as exc:
                    await self._close_quiet()
                    if attempt == 2:
                        raise RconError(f"falha no RCON: {exc or type(exc).__name__}") from exc
        raise RconError("falha no RCON")  # inalcançável; satisfaz o type checker

    async def close(self) -> None:
        async with self._lock:
            await self._close_quiet()
