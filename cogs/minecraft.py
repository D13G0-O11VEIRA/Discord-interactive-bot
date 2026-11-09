"""Cog de Minecraft: status, início do servidor, painel ao vivo e ponte de chat.

Carregada automaticamente pelo bot.py (ele carrega tudo que está em cogs/).
Configuração pelo .env (veja .env.example) e por /mc configurar.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from services.minecraft.config import MinecraftConfig
from services.minecraft.controllers import (
    ActionResult,
    AternosLinkController,
    LocalProcessController,
    ServerController,
)
from services.minecraft.events import (
    ADVANCEMENT,
    CHAT,
    DEATH,
    JOIN,
    LEAVE,
    SERVER_STARTED,
    SERVER_STOPPING,
    McEvent,
    clean_for_minecraft,
)
from services.minecraft.monitor import OFFLINE, ONLINE, TRANSITION, ServerMonitor, Snapshot
from services.minecraft.rcon import RconClient, RconError
from services.minecraft.slp import strip_formatting
from services.minecraft.store import JsonStore
from services.minecraft.transports import (
    BridgeTransport,
    LocalTransport,
    PluginTransport,
    TransportNotSupported,
)

logger = logging.getLogger("DiscordBot.minecraft")

START_COOLDOWN = 30  # segundos entre dois /mc start do mesmo usuário
WAITER_TTL = 15 * 60  # por quanto tempo esperamos o servidor ficar online após /mc start
PLAYER_NAME_RE = re.compile(r"^[.\w]{3,32}$")  # nomes Java (3-16) e Bedrock/Floodgate (até 32, prefixo '.')

STATE_COLORS = {
    ONLINE: discord.Color.green(),
    OFFLINE: discord.Color.red(),
    TRANSITION: discord.Color.orange(),
}
STATE_LABELS = {
    ONLINE: "🟢 Online",
    OFFLINE: "🔴 Offline",
    TRANSITION: "🟠 Iniciando / desligando",
}


class Minecraft(commands.Cog):
    """Comandos /mc e ponte de chat entre o Minecraft e o Discord."""

    mc = app_commands.Group(
        name="mc",
        description="Servidor de Minecraft",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cfg = MinecraftConfig.from_env()  # ValueError aqui desativa a cog (veja setup)
        self.store = JsonStore(Path("data") / "minecraft.json")
        self.monitor = ServerMonitor(self.cfg)

        self.rcon: RconClient | None = None
        if self.cfg.rcon_password:
            self.rcon = RconClient(self.cfg.rcon_host, self.cfg.rcon_port, self.cfg.rcon_password)

        self.controller: ServerController
        if self.cfg.provider == "local":
            self.controller = LocalProcessController(self.cfg.start_command, self.cfg.server_dir, self.rcon)
        else:
            self.controller = AternosLinkController(self.cfg.aternos_url)

        self.transport: BridgeTransport | None = None
        if self.cfg.bridge_mode == "plugin":
            self.transport = PluginTransport(self.cfg.ws_host, self.cfg.ws_port, self.cfg.ws_token)
        elif self.cfg.bridge_mode == "local" and self.cfg.log_file and self.rcon:
            self.transport = LocalTransport(self.cfg.log_file, self.rcon)

        self._session: aiohttp.ClientSession | None = None
        self._webhook: discord.Webhook | None = None
        self._tasks: list[asyncio.Task] = []
        self._wake = asyncio.Event()  # acorda o monitor antes do intervalo normal
        self._waiters: dict[int, tuple[int, float]] = {}  # user_id -> (channel_id, quando pediu)
        self._last_start: dict[int, float] = {}
        self._panel_signature: tuple | None = None
        self._presence_text: str | None = None

    # ------------------------------------------------------------------ ciclo de vida
    async def cog_load(self) -> None:
        self._session = aiohttp.ClientSession()
        if self.cfg.webhook_url:
            self._webhook = discord.Webhook.from_url(self.cfg.webhook_url, session=self._session)
        if self.transport is not None:
            self.transport.set_handler(self._on_mc_event)
            try:
                await self.transport.start()
            except Exception:  # noqa: BLE001 - porta ocupada, arquivo inexistente etc.
                logger.exception("Não consegui iniciar a ponte de chat; ela ficará desativada")
                self.transport = None
        self._tasks.append(asyncio.create_task(self._status_loop(), name="mc-status"))

    async def cog_unload(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self.transport is not None:
            await self.transport.stop()
        elif self.rcon is not None:
            await self.rcon.close()
        if self._session is not None:
            await self._session.close()

    # ------------------------------------------------------------------ auxiliares
    def _bridge_channel_id(self) -> int | None:
        return self.store.get("bridge_channel_id", self.cfg.bridge_channel_id)

    def _notify_channel_id(self) -> int | None:
        return self.store.get("notify_channel_id", self.cfg.notify_channel_id)

    def _notify_role_id(self) -> int | None:
        return self.store.get("notify_role_id", self.cfg.notify_role_id)

    async def _get_channel(self, channel_id: int | None) -> discord.abc.Messageable | None:
        if channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            logger.warning("Canal %s não encontrado ou sem acesso", channel_id)
            return None

    @staticmethod
    def _is_admin(interaction: discord.Interaction) -> bool:
        user = interaction.user
        return isinstance(user, discord.Member) and user.guild_permissions.manage_guild

    @staticmethod
    async def _deny(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Você precisa da permissão **Gerenciar Servidor** para usar este comando.", ephemeral=True
        )

    def _player_names(self, snapshot: Snapshot) -> list[str]:
        if self.transport is not None and self.transport.players is not None:
            return self.transport.players
        return snapshot.status.player_names if snapshot.status else []

    def _status_embed(self, snapshot: Snapshot) -> discord.Embed:
        embed = discord.Embed(title="Servidor de Minecraft", color=STATE_COLORS[snapshot.state])
        embed.add_field(name="Estado", value=STATE_LABELS[snapshot.state])
        embed.add_field(name="Endereço", value=f"`{self.cfg.address}`")
        status = snapshot.status
        if snapshot.state == ONLINE and status is not None:
            embed.add_field(name="Jogadores", value=f"{status.players_online}/{status.players_max}")
            names = self._player_names(snapshot)
            if names:
                embed.add_field(name="Online agora", value=", ".join(names)[:1000], inline=False)
            embed.add_field(name="Versão", value=status.version_name or "?")
            if status.latency_ms is not None:
                embed.add_field(name="Latência", value=f"{status.latency_ms} ms")
            if status.motd:
                embed.description = status.motd[:300]
        elif snapshot.state == OFFLINE:
            embed.description = "Use `/mc start` para ligar."
        embed.set_footer(text="Atualizado")
        embed.timestamp = discord.utils.utcnow()
        return embed

    # ------------------------------------------------------------------ monitor de status
    async def _status_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Erro no monitor de status")
            # Enquanto alguém espera o servidor ligar, consulta com mais frequência.
            interval = min(10, self.cfg.status_interval) if self._waiters else self.cfg.status_interval
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _tick(self) -> None:
        previous = self.monitor.snapshot
        current = await self.monitor.refresh()
        if previous is not None and previous.state != current.state:
            await self._announce_state(previous, current)
        self._prune_waiters()
        if current.state == ONLINE:
            await self._notify_waiters()
        await self._update_panel(current)
        await self._update_presence(current)

    async def _announce_state(self, previous: Snapshot, current: Snapshot) -> None:
        channel = await self._get_channel(self._notify_channel_id())
        if channel is None:
            return
        if current.state == ONLINE:
            embed = self._status_embed(current)
            embed.title = "🟢 O servidor está online!"
            role_id = self._notify_role_id()
            mentions = discord.AllowedMentions(roles=[discord.Object(id=role_id)]) if role_id else None
            content = f"<@&{role_id}>" if role_id else None
            await channel.send(content=content, embed=embed, allowed_mentions=mentions or discord.AllowedMentions.none())
        elif previous.state == ONLINE:
            await channel.send(embed=discord.Embed(title="🔴 O servidor foi desligado", color=discord.Color.red()))

    def _prune_waiters(self) -> None:
        now = time.time()
        self._waiters = {u: w for u, w in self._waiters.items() if now - w[1] <= WAITER_TTL}

    async def _notify_waiters(self) -> None:
        waiters, self._waiters = self._waiters, {}
        for user_id, (channel_id, _) in waiters.items():
            channel = await self._get_channel(channel_id)
            if channel is not None:
                await channel.send(
                    f"<@{user_id}> o servidor está online! `{self.cfg.address}`",
                    allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=user_id)]),
                )

    async def _update_panel(self, snapshot: Snapshot) -> None:
        panel = self.store.get("panel")
        if not panel:
            return
        signature = (snapshot.state, snapshot.status.players_online if snapshot.status else None,
                     tuple(self._player_names(snapshot)))
        if signature == self._panel_signature:
            return
        channel = await self._get_channel(panel["channel_id"])
        if channel is None:
            return
        try:
            message = await channel.fetch_message(panel["message_id"])
            await message.edit(embed=self._status_embed(snapshot))
            self._panel_signature = signature
        except discord.NotFound:
            logger.info("Mensagem do painel apagada; removendo do registro")
            self.store.set("panel", None)
        except discord.HTTPException:
            logger.warning("Não consegui atualizar o painel", exc_info=True)

    async def _update_presence(self, snapshot: Snapshot) -> None:
        if not self.cfg.update_presence:
            return
        if snapshot.state == ONLINE and snapshot.status is not None:
            text = f"Minecraft • {snapshot.status.players_online}/{snapshot.status.players_max}"
        else:
            text = "Minecraft • offline"
        if text != self._presence_text:
            self._presence_text = text
            await self.bot.change_presence(activity=discord.Game(name=text))

    # ------------------------------------------------------------------ ponte: Minecraft -> Discord
    async def _on_mc_event(self, event: McEvent) -> None:
        if event.kind in (SERVER_STARTED, SERVER_STOPPING):
            self._wake.set()  # confirma o estado agora em vez de esperar o próximo ciclo
            return
        channel = await self._get_channel(self._bridge_channel_id())
        if channel is None:
            return
        none = discord.AllowedMentions.none()
        name = discord.utils.escape_markdown(event.player)

        if event.kind == CHAT:
            await self._send_chat_to_discord(channel, event)
        elif event.kind == JOIN and self.cfg.show_joins:
            await channel.send(f"➡️ **{name}** entrou no servidor.", allowed_mentions=none)
        elif event.kind == LEAVE and self.cfg.show_joins:
            await channel.send(f"⬅️ **{name}** saiu do servidor.", allowed_mentions=none)
        elif event.kind == DEATH and self.cfg.show_deaths:
            await channel.send(f"💀 {discord.utils.escape_markdown(event.text)}", allowed_mentions=none)
        elif event.kind == ADVANCEMENT and self.cfg.show_advancements:
            await channel.send(f"🏆 **{name}** conquistou **{discord.utils.escape_markdown(event.text)}**",
                               allowed_mentions=none)

    async def _send_chat_to_discord(self, channel: discord.abc.Messageable, event: McEvent) -> None:
        text = event.text[:1900]
        if self._webhook is not None:
            try:
                await self._webhook.send(
                    content=text,
                    username=event.player[:80],
                    avatar_url=f"https://minotar.net/helm/{quote(event.player)}/128.png",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            except discord.HTTPException:
                logger.warning("Webhook recusou a mensagem; usando mensagem comum", exc_info=True)
        await channel.send(
            f"**{discord.utils.escape_markdown(event.player)}** » {text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ------------------------------------------------------------------ ponte: Discord -> Minecraft
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if self.transport is None or message.author.bot or message.guild is None:
            return
        if message.channel.id != self._bridge_channel_id():
            return
        text = clean_for_minecraft(message.clean_content)
        if message.attachments:
            text = f"{text} [anexo]".strip()
        if not text:
            return
        if not await self.transport.send_chat(message.author.display_name, text):
            try:
                await message.add_reaction("⚠️")  # o jogo não recebeu (servidor desligado?)
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------------ comandos
    @mc.command(name="status", description="Mostra se o servidor está online e quem está jogando.")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        snapshot = await self.monitor.refresh()
        await interaction.followup.send(embed=self._status_embed(snapshot))

    @mc.command(name="ip", description="Mostra o endereço do servidor.")
    async def ip(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(f"`{self.cfg.address}`")

    @mc.command(name="jogadores", description="Lista quem está jogando agora.")
    async def players(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        snapshot = await self.monitor.refresh()
        if snapshot.state != ONLINE or snapshot.status is None:
            await interaction.followup.send("O servidor não está online no momento.")
            return
        names = self._player_names(snapshot)
        total = f"{snapshot.status.players_online}/{snapshot.status.players_max}"
        if names:
            listed = ", ".join(f"`{n}`" for n in names)
            await interaction.followup.send(f"**Jogadores ({total}):** {listed}"[:1900])
        elif snapshot.status.players_online:
            await interaction.followup.send(f"**Jogadores ({total})** — o servidor não divulgou os nomes.")
        else:
            await interaction.followup.send("Ninguém online no momento.")

    @mc.command(name="start", description="Liga o servidor (no Aternos, abre o painel) e avisa quando ficar online.")
    async def start(self, interaction: discord.Interaction) -> None:
        now = time.monotonic()
        if now - self._last_start.get(interaction.user.id, 0.0) < START_COOLDOWN:
            await interaction.response.send_message("Calma! Espere alguns segundos para pedir de novo.", ephemeral=True)
            return
        self._last_start[interaction.user.id] = now

        await interaction.response.defer(thinking=True)
        snapshot = await self.monitor.refresh()
        if snapshot.state == ONLINE:
            await interaction.followup.send(f"O servidor já está online! `{self.cfg.address}`")
            return
        if snapshot.state == TRANSITION:
            self._remember_waiter(interaction)
            await interaction.followup.send("O servidor já está iniciando. Aviso aqui quando ficar online.")
            return

        result = await self.controller.start()
        if result.ok or result.manual:
            self._remember_waiter(interaction)
            self._wake.set()
        await self._send_action_result(interaction, result, "Ligar servidor")

    @mc.command(name="stop", description="Desliga o servidor (somente quem gerencia o servidor).")
    async def stop(self, interaction: discord.Interaction) -> None:
        if not self._is_admin(interaction):
            await self._deny(interaction)
            return
        await interaction.response.defer(thinking=True)
        result = await self.controller.stop()
        if result.ok:
            self._wake.set()
        await self._send_action_result(interaction, result, "Desligar servidor")

    def _remember_waiter(self, interaction: discord.Interaction) -> None:
        if interaction.channel_id is not None:
            self._waiters[interaction.user.id] = (interaction.channel_id, time.time())

    @staticmethod
    async def _send_action_result(interaction: discord.Interaction, result: ActionResult, title: str) -> None:
        color = discord.Color.green() if result.ok else discord.Color.orange() if result.manual else discord.Color.red()
        embed = discord.Embed(title=title, description=result.message, color=color)
        view = discord.utils.MISSING
        if result.url:
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Abrir painel", style=discord.ButtonStyle.link, url=result.url))
        await interaction.followup.send(embed=embed, view=view)

    @mc.command(name="cmd", description="Executa um comando no console do servidor (RCON).")
    @app_commands.describe(comando="Comando sem a barra, ex.: say Olá")
    async def console(self, interaction: discord.Interaction, comando: str) -> None:
        if not self._is_admin(interaction):
            await self._deny(interaction)
            return
        if not self.cfg.allow_console:
            await interaction.response.send_message(
                "O console remoto está desativado. Defina `MC_ALLOW_CONSOLE=true` no .env se quiser usá-lo.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            output = await self._run_command(comando.lstrip("/"))
        except (RconError, TransportNotSupported) as exc:
            await interaction.followup.send(f"Não consegui executar: {exc}", ephemeral=True)
            return
        output = strip_formatting(output).strip() or "(sem resposta)"
        await interaction.followup.send(f"```\n{output[:1800]}\n```", ephemeral=True)
        logger.info("%s (%s) executou no console: %s", interaction.user, interaction.user.id, comando)

    async def _run_command(self, command: str) -> str:
        if self.transport is not None and self.transport.supports_commands:
            return await self.transport.run_command(command)
        if self.rcon is None:
            raise TransportNotSupported("RCON não configurado (MC_RCON_PASSWORD)")
        return await self.rcon.command(command)

    @mc.command(name="whitelist", description="Gerencia a whitelist do servidor (RCON).")
    @app_commands.describe(acao="O que fazer", jogador="Nome do jogador (não precisa para listar)")
    @app_commands.choices(
        acao=[
            app_commands.Choice(name="adicionar", value="add"),
            app_commands.Choice(name="remover", value="remove"),
            app_commands.Choice(name="listar", value="list"),
        ]
    )
    async def whitelist(
        self,
        interaction: discord.Interaction,
        acao: app_commands.Choice[str],
        jogador: Optional[str] = None,
    ) -> None:
        if not self._is_admin(interaction):
            await self._deny(interaction)
            return
        if acao.value != "list" and not (jogador and PLAYER_NAME_RE.match(jogador)):
            await interaction.response.send_message("Informe um nome de jogador válido.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        command = "whitelist list" if acao.value == "list" else f"whitelist {acao.value} {jogador}"
        try:
            output = await self._run_command(command)
        except (RconError, TransportNotSupported) as exc:
            await interaction.followup.send(f"Não consegui executar: {exc}", ephemeral=True)
            return
        await interaction.followup.send(strip_formatting(output).strip()[:1900] or "Feito.", ephemeral=True)

    @mc.command(name="configurar", description="Define canais e cargo usados pelo bot (Gerenciar Servidor).")
    @app_commands.describe(
        ponte="Canal onde o chat do jogo e do Discord se misturam",
        avisos="Canal que recebe os avisos de servidor ligado/desligado",
        cargo="Cargo mencionado quando o servidor ficar online",
    )
    async def configure(
        self,
        interaction: discord.Interaction,
        ponte: Optional[discord.TextChannel] = None,
        avisos: Optional[discord.TextChannel] = None,
        cargo: Optional[discord.Role] = None,
    ) -> None:
        if not self._is_admin(interaction):
            await self._deny(interaction)
            return
        changes = []
        if ponte is not None:
            self.store.set("bridge_channel_id", ponte.id)
            changes.append(f"ponte → {ponte.mention}")
        if avisos is not None:
            self.store.set("notify_channel_id", avisos.id)
            changes.append(f"avisos → {avisos.mention}")
        if cargo is not None:
            self.store.set("notify_role_id", cargo.id)
            changes.append(f"cargo → {cargo.mention}")
        text = "Salvo: " + ", ".join(changes) if changes else "Nada para alterar. Informe ao menos um parâmetro."
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @mc.command(name="painel", description="Cria uma mensagem de status que se atualiza sozinha neste canal.")
    async def panel(self, interaction: discord.Interaction) -> None:
        if not self._is_admin(interaction):
            await self._deny(interaction)
            return
        await interaction.response.defer(ephemeral=True)
        snapshot = self.monitor.snapshot or await self.monitor.refresh()
        message = await interaction.channel.send(embed=self._status_embed(snapshot))
        self.store.set("panel", {"channel_id": message.channel.id, "message_id": message.id})
        self._panel_signature = None
        await interaction.followup.send("Painel criado. Ele será atualizado automaticamente.", ephemeral=True)

    @mc.command(name="ponte", description="Mostra o estado da ponte de chat.")
    async def bridge(self, interaction: discord.Interaction) -> None:
        if self.transport is None:
            await interaction.response.send_message(
                "A ponte de chat está desativada (`MC_BRIDGE_MODE=off`).", ephemeral=True
            )
            return
        channel_id = self._bridge_channel_id()
        channel = f"<#{channel_id}>" if channel_id else "não definido (use `/mc configurar`)"
        link = "🟢 conectada" if self.transport.connected else "🔴 aguardando conexão"
        await interaction.response.send_message(
            f"**Modo:** {self.transport.name}\n**Conexão:** {link}\n**Canal:** {channel}",
            ephemeral=True,
        )

    @mc.command(name="raw", description="Mostra a resposta bruta do servidor (para ajustar a detecção no Aternos).")
    async def raw(self, interaction: discord.Interaction) -> None:
        if not self._is_admin(interaction):
            await self._deny(interaction)
            return
        await interaction.response.defer(ephemeral=True)
        snapshot = await self.monitor.refresh()
        if snapshot.status is None:
            await interaction.followup.send(f"Sem resposta do servidor: `{snapshot.error}`", ephemeral=True)
            return
        s = snapshot.status
        lines = [
            f"estado detectado : {snapshot.state}",
            f"versão (nome)    : {s.version_name!r}",
            f"protocolo        : {s.protocol}",
            f"jogadores        : {s.players_online}/{s.players_max}",
            f"motd             : {s.motd!r}",
            f"hints offline    : {', '.join(self.cfg.offline_hints)}",
            f"hints transição  : {', '.join(self.cfg.transition_hints)}",
        ]
        await interaction.followup.send("```\n" + "\n".join(lines)[:1800] + "\n```", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    try:
        cog = Minecraft(bot)
    except ValueError as exc:
        logger.error("Módulo de Minecraft desativado: %s", exc)
        return
    await bot.add_cog(cog)
