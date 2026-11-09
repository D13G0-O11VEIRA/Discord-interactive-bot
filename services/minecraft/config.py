"""Configuração do módulo de Minecraft, lida das variáveis de ambiente (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 25565


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int | None = None) -> int | None:
    raw = _str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} precisa ser um número inteiro (valor atual: {raw!r})") from None


def _bool(name: str, default: bool = False) -> bool:
    raw = _str(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "sim", "on"}


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(p.strip().lower() for p in _str(name, default).split(",") if p.strip())


def split_address(address: str) -> tuple[str, int]:
    """Separa 'host:porta' em (host, porta). Sem porta, usa 25565."""
    address = address.strip()
    if ":" in address:
        host, _, port = address.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            raise ValueError(f"Porta inválida em MC_ADDRESS: {address!r}") from None
    return address, DEFAULT_PORT


@dataclass(frozen=True)
class MinecraftConfig:
    # --- servidor ---
    provider: str  # "aternos" (só link + monitoramento) ou "local" (processo na mesma máquina)
    host: str
    port: int
    aternos_url: str
    offline_hints: tuple[str, ...]
    transition_hints: tuple[str, ...]
    status_interval: int
    update_presence: bool

    # --- servidor local ---
    start_command: str
    server_dir: Path | None
    log_file: Path | None
    rcon_host: str
    rcon_port: int
    rcon_password: str
    allow_console: bool

    # --- ponte de chat ---
    bridge_mode: str  # "off", "plugin" (plugin conecta no bot) ou "local" (log + RCON)
    ws_host: str
    ws_port: int
    ws_token: str
    bridge_channel_id: int | None
    notify_channel_id: int | None
    notify_role_id: int | None
    webhook_url: str
    show_joins: bool
    show_deaths: bool
    show_advancements: bool

    @property
    def address(self) -> str:
        return self.host if self.port == DEFAULT_PORT else f"{self.host}:{self.port}"

    @classmethod
    def from_env(cls) -> "MinecraftConfig":
        address = _str("MC_ADDRESS")
        if not address:
            raise ValueError("defina MC_ADDRESS no .env (ex.: meuservidor.aternos.me:12345)")
        host, port = split_address(address)

        provider = _str("MC_PROVIDER", "aternos").lower()
        if provider not in {"aternos", "local"}:
            raise ValueError("MC_PROVIDER deve ser 'aternos' ou 'local'")

        server_dir = Path(_str("MC_SERVER_DIR")) if _str("MC_SERVER_DIR") else None
        log_raw = _str("MC_LOG_FILE")
        log_file = Path(log_raw) if log_raw else (server_dir / "logs" / "latest.log" if server_dir else None)

        bridge_mode = _str("MC_BRIDGE_MODE", "off").lower()
        if bridge_mode not in {"off", "plugin", "local"}:
            raise ValueError("MC_BRIDGE_MODE deve ser 'off', 'plugin' ou 'local'")

        config = cls(
            provider=provider,
            host=host,
            port=port,
            aternos_url=_str("MC_ATERNOS_URL", "https://aternos.org/servers/"),
            offline_hints=_csv("MC_OFFLINE_HINTS", "offline"),
            transition_hints=_csv(
                "MC_TRANSITION_HINTS",
                "starting,loading,preparing,saving,stopping,queue,waiting,iniciando,carregando",
            ),
            status_interval=max(10, _int("MC_STATUS_INTERVAL", 30) or 30),
            update_presence=_bool("MC_UPDATE_PRESENCE", False),
            start_command=_str("MC_START_COMMAND"),
            server_dir=server_dir,
            log_file=log_file,
            rcon_host=_str("MC_RCON_HOST", "127.0.0.1"),
            rcon_port=_int("MC_RCON_PORT", 25575) or 25575,
            rcon_password=_str("MC_RCON_PASSWORD"),
            allow_console=_bool("MC_ALLOW_CONSOLE", False),
            bridge_mode=bridge_mode,
            ws_host=_str("MC_WS_HOST", "0.0.0.0"),
            ws_port=_int("MC_WS_PORT", 8765) or 8765,
            ws_token=_str("MC_WS_TOKEN"),
            bridge_channel_id=_int("MC_BRIDGE_CHANNEL_ID"),
            notify_channel_id=_int("MC_NOTIFY_CHANNEL_ID"),
            notify_role_id=_int("MC_NOTIFY_ROLE_ID"),
            webhook_url=_str("MC_BRIDGE_WEBHOOK_URL"),
            show_joins=_bool("MC_SHOW_JOINS", True),
            show_deaths=_bool("MC_SHOW_DEATHS", True),
            show_advancements=_bool("MC_SHOW_ADVANCEMENTS", False),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.bridge_mode == "plugin" and len(self.ws_token) < 16:
            raise ValueError("com MC_BRIDGE_MODE=plugin, MC_WS_TOKEN precisa ter pelo menos 16 caracteres")
        if self.bridge_mode == "local":
            if self.log_file is None:
                raise ValueError("com MC_BRIDGE_MODE=local, defina MC_LOG_FILE ou MC_SERVER_DIR")
            if not self.rcon_password:
                raise ValueError("com MC_BRIDGE_MODE=local, defina MC_RCON_PASSWORD")
        if self.provider == "local" and not self.start_command:
            raise ValueError("com MC_PROVIDER=local, defina MC_START_COMMAND")
