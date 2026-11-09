"""Controladores de servidor: o que significa "ligar" e "desligar" em cada hospedagem."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from services.minecraft.rcon import RconClient, RconError

logger = logging.getLogger("DiscordBot.minecraft.controller")


@dataclass(frozen=True)
class ActionResult:
    ok: bool  # a ação foi executada pelo bot
    message: str
    manual: bool = False  # o usuário precisa concluir a ação sozinho
    url: str | None = None  # link para o painel, quando for o caso


class ServerController(ABC):
    @abstractmethod
    async def start(self) -> ActionResult: ...

    @abstractmethod
    async def stop(self) -> ActionResult: ...


class AternosLinkController(ServerController):
    """Aternos: não existe API oficial e os Termos de Uso proíbem automatizar o site.

    Por isso o bot não clica "Iniciar" por você: ele entrega o link do painel e depois
    monitora o endereço, avisando no Discord assim que o servidor ficar online.
    """

    def __init__(self, panel_url: str) -> None:
        self.panel_url = panel_url

    async def start(self) -> ActionResult:
        return ActionResult(
            ok=False,
            manual=True,
            url=self.panel_url,
            message=(
                "O Aternos não oferece API para ligar o servidor por bot. "
                "Abra o painel, clique em **Iniciar** e eu aviso aqui quando ele estiver online."
            ),
        )

    async def stop(self) -> ActionResult:
        return ActionResult(
            ok=False,
            manual=True,
            url=self.panel_url,
            message="No Aternos o servidor é desligado pelo painel (ou fecha sozinho quando fica vazio).",
        )


class LocalProcessController(ServerController):
    """Servidor rodando na mesma máquina do bot: inicia o processo e para via RCON."""

    def __init__(self, start_command: str, server_dir: Path | None, rcon: RconClient | None) -> None:
        self.start_command = start_command
        self.server_dir = server_dir
        self.rcon = rcon

    def _argv(self) -> list[str]:
        parts = shlex.split(self.start_command, posix=os.name != "nt")
        return [part.strip('"') for part in parts]  # no Windows o shlex mantém as aspas

    async def start(self) -> ActionResult:
        kwargs: dict = {}
        if os.name == "nt":
            # Destaca o processo para o servidor continuar rodando se o bot for reiniciado.
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._argv(),
                cwd=str(self.server_dir) if self.server_dir else None,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                **kwargs,
            )
        except (OSError, ValueError) as exc:
            logger.exception("Falha ao iniciar o servidor")
            return ActionResult(ok=False, message=f"Não consegui iniciar o servidor: {exc}")
        logger.info("Servidor iniciado (pid %s)", proc.pid)
        return ActionResult(ok=True, message="Servidor iniciando. Pode levar um minuto até aceitar conexões.")

    async def stop(self) -> ActionResult:
        if self.rcon is None:
            return ActionResult(ok=False, message="Defina MC_RCON_PASSWORD para eu poder desligar o servidor.")
        try:
            await self.rcon.command("stop")
        except RconError as exc:
            return ActionResult(ok=False, message=f"Não consegui falar com o RCON: {exc}")
        return ActionResult(ok=True, message="Comando de desligamento enviado.")
