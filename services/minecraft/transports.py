"""Transportes da ponte de chat entre o Minecraft e o Discord.

Dois jeitos de ligar o servidor ao bot:

* ``PluginTransport``  - um plugin dentro do servidor (pasta ``companion/``) conecta
  no bot por WebSocket. É o único caminho que funciona no Aternos, porque lá não há
  acesso ao log nem ao RCON.
* ``LocalTransport``   - o bot lê o ``latest.log`` e fala com o jogo por RCON. Serve
  para servidor rodando na mesma máquina do bot (ou com a pasta acessível).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from pathlib import Path

from services.minecraft.events import (
    ADVANCEMENT,
    CHAT,
    DEATH,
    JOIN,
    LEAVE,
    SERVER_STARTED,
    SERVER_STOPPING,
    McEvent,
    pretty_advancement,
    tellraw_command,
)
from services.minecraft.logparser import parse_line
from services.minecraft.rcon import RconClient, RconError

logger = logging.getLogger("DiscordBot.minecraft.transport")

EventHandler = Callable[[McEvent], Awaitable[None]]

_MAX_TEXT = 500
_LIST_RE = re.compile(r"players online:\s*(?P<names>.*)$", re.IGNORECASE | re.DOTALL)


class TransportNotSupported(Exception):
    """A operação não existe neste tipo de transporte."""


class BridgeTransport(ABC):
    """Interface comum: recebe eventos do jogo e envia chat do Discord para o jogo."""

    name = "base"
    supports_commands = False

    def __init__(self) -> None:
        self._handler: EventHandler | None = None

    def set_handler(self, handler: EventHandler) -> None:
        self._handler = handler

    async def _emit(self, event: McEvent) -> None:
        if self._handler is None:
            return
        try:
            await self._handler(event)
        except Exception:  # noqa: BLE001 - erro em um evento não pode derrubar o transporte
            logger.exception("Erro ao tratar evento %s", event.kind)

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @property
    def players(self) -> list[str] | None:
        """Lista de jogadores conhecida pelo transporte (None se ele não sabe)."""
        return None

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send_chat(self, author: str, text: str) -> bool:
        """Envia uma mensagem do Discord ao chat do jogo. Devolve False se não conseguiu."""

    async def run_command(self, command: str) -> str:
        raise TransportNotSupported("este transporte não executa comandos")


# ---------------------------------------------------------------------------
# Local: log + RCON
# ---------------------------------------------------------------------------
class LocalTransport(BridgeTransport):
    name = "local (log + RCON)"
    supports_commands = True

    def __init__(self, log_file: Path, rcon: RconClient, poll_interval: float = 0.5) -> None:
        super().__init__()
        self.log_file = log_file
        self.rcon = rcon
        self.poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._pos: int | None = None
        self._ident: tuple[int, int] | None = None
        self._partial = b""
        self._players: set[str] = set()

    @property
    def connected(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def players(self) -> list[str] | None:
        return sorted(self._players)

    # -- leitura do log -----------------------------------------------------
    def read_new_lines(self) -> list[str]:
        """Lê o que foi acrescentado ao log desde a última chamada (trata rotação do arquivo)."""
        try:
            stat = self.log_file.stat()
        except FileNotFoundError:
            self._pos = self._ident = None
            self._partial = b""
            return []

        ident = (stat.st_dev, stat.st_ino)
        if self._pos is None:
            self._pos, self._ident = stat.st_size, ident  # começa do fim: não repete o passado
            return []
        if ident != self._ident or stat.st_size < self._pos:
            self._pos, self._ident, self._partial = 0, ident, b""  # arquivo novo (servidor reiniciou)
        if stat.st_size == self._pos:
            return []

        with open(self.log_file, "rb") as handle:
            handle.seek(self._pos)
            chunk = handle.read()
            self._pos = handle.tell()

        data = self._partial + chunk
        *lines, self._partial = data.split(b"\n")
        return [line.decode("utf-8", errors="replace") for line in lines]

    async def _track_players(self, event: McEvent) -> None:
        if event.kind == JOIN:
            self._players.add(event.player)
        elif event.kind == LEAVE:
            self._players.discard(event.player)
        elif event.kind in (SERVER_STARTED, SERVER_STOPPING):
            self._players.clear()

    async def _tail(self) -> None:
        while True:
            try:
                for line in self.read_new_lines():
                    event = parse_line(line)
                    if event is not None:
                        await self._track_players(event)
                        await self._emit(event)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Erro lendo %s", self.log_file)
            await asyncio.sleep(self.poll_interval)

    # -- API ----------------------------------------------------------------
    async def start(self) -> None:
        self._task = asyncio.create_task(self._tail(), name="mc-log-tail")
        logger.info("Ponte local lendo %s", self.log_file)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self.rcon.close()

    async def send_chat(self, author: str, text: str) -> bool:
        try:
            await self.rcon.command(tellraw_command(author, text))
            return True
        except RconError as exc:
            logger.warning("Não consegui enviar o chat por RCON: %s", exc)
            return False

    async def run_command(self, command: str) -> str:
        return await self.rcon.command(command)

    async def list_players(self) -> list[str]:
        """Pergunta ao servidor quem está online (comando 'list')."""
        reply = await self.rcon.command("list")
        match = _LIST_RE.search(reply)
        names = match.group("names").strip() if match else ""
        return [n.strip() for n in names.split(",") if n.strip()]


# ---------------------------------------------------------------------------
# Plugin: WebSocket
# ---------------------------------------------------------------------------
def parse_plugin_message(data: object) -> McEvent | None:
    """Traduz uma mensagem JSON do plugin em McEvent (None para tipos que não são eventos)."""
    if not isinstance(data, dict):
        return None
    kind = data.get("type")
    player = str(data.get("player", ""))[:64]

    if kind == CHAT:
        text = str(data.get("message", ""))[:_MAX_TEXT].strip()
        return McEvent(CHAT, player, text) if player and text else None
    if kind in (JOIN, LEAVE):
        return McEvent(kind, player) if player else None
    if kind == DEATH:
        text = str(data.get("message", ""))[:_MAX_TEXT].strip()
        return McEvent(DEATH, player, text or f"{player} morreu") if player else None
    if kind == ADVANCEMENT:
        key = str(data.get("advancement", ""))[:128]
        if not player or not key or key.startswith("recipes/") or key.endswith("/root"):
            return None
        return McEvent(ADVANCEMENT, player, pretty_advancement(key))
    if kind in (SERVER_STARTED, SERVER_STOPPING):
        return McEvent(kind)
    return None


class PluginTransport(BridgeTransport):
    name = "plugin (WebSocket)"

    def __init__(self, host: str, port: int, token: str) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self._token = token.encode("utf-8")
        self._ws = None
        self._runner = None
        self._players: list[str] | None = None

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    @property
    def players(self) -> list[str] | None:
        return list(self._players) if self.connected and self._players is not None else None

    async def start(self) -> None:
        from aiohttp import web  # já vem como dependência do discord.py

        app = web.Application()
        app.router.add_get("/ws", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self.port).start()
        logger.info("Ponte aguardando o plugin em ws://%s:%s/ws", self.host, self.port)

    async def stop(self) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def send_chat(self, author: str, text: str) -> bool:
        if not self.connected:
            return False
        try:
            await self._ws.send_json({"type": CHAT, "author": author[:32], "message": text})
            return True
        except (ConnectionError, RuntimeError):
            return False

    async def _handle(self, request):
        from aiohttp import WSMsgType, web

        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=64 * 1024)
        await ws.prepare(request)

        try:
            hello = await asyncio.wait_for(ws.receive_json(), timeout=10)
        except Exception:  # noqa: BLE001 - qualquer coisa fora do combinado derruba a conexão
            await ws.close(code=4000, message=b"hello esperado")
            return ws
        supplied = str(hello.get("token", "")).encode("utf-8") if isinstance(hello, dict) else b""
        if not (isinstance(hello, dict) and hello.get("type") == "hello" and hmac.compare_digest(supplied, self._token)):
            logger.warning("Conexão do plugin recusada (token inválido) de %s", request.remote)
            await ws.close(code=4001, message=b"token invalido")
            return ws

        previous, self._ws = self._ws, ws  # só uma conexão por vez: a nova substitui a antiga
        if previous is not None and not previous.closed:
            await previous.close(code=4002, message=b"substituida")
        logger.info("Plugin conectado: %s", hello.get("server", "?"))

        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    await self._on_text(message.data)
                elif message.type == WSMsgType.ERROR:
                    break
        finally:
            if self._ws is ws:
                self._ws = None
                self._players = None
                logger.info("Plugin desconectado")
        return ws

    async def _on_text(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(data, dict) and data.get("type") == "players":
            names = data.get("players")
            if isinstance(names, list):
                self._players = [str(n) for n in names][:200]
            return
        event = parse_plugin_message(data)
        if event is not None:
            await self._emit(event)
