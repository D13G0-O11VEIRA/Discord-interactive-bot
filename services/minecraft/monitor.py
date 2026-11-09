"""Monitor de estado do servidor (online / offline / em transição)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from services.minecraft import slp
from services.minecraft.config import MinecraftConfig

logger = logging.getLogger("DiscordBot.minecraft.monitor")

ONLINE = "online"
OFFLINE = "offline"
TRANSITION = "transition"  # iniciando, salvando, desligando, na fila...


@dataclass
class Snapshot:
    state: str
    status: slp.ServerStatus | None
    error: str | None
    checked_at: float


def classify(
    status: slp.ServerStatus,
    offline_hints: tuple[str, ...],
    transition_hints: tuple[str, ...],
) -> str:
    """Decide o estado a partir da resposta do ping.

    O Aternos mantém o endereço respondendo mesmo com o servidor desligado: a "resposta"
    vem de um servidor de espera, e o que muda é o texto da versão/MOTD. Por isso olhamos
    esses dois campos em busca das palavras configuradas em MC_OFFLINE_HINTS e
    MC_TRANSITION_HINTS (use /mc raw para ver exatamente o que o seu endereço devolve).
    """
    text = f"{status.version_name} {status.motd}".lower()
    if any(hint in text for hint in transition_hints):
        return TRANSITION
    if any(hint in text for hint in offline_hints):
        return OFFLINE
    return ONLINE


class ServerMonitor:
    def __init__(self, config: MinecraftConfig) -> None:
        self.config = config
        self.snapshot: Snapshot | None = None

    async def refresh(self) -> Snapshot:
        try:
            status = await slp.ping(self.config.host, self.config.port)
        except (OSError, TimeoutError, ValueError, EOFError) as exc:
            # Sem resposta nenhuma (conexão recusada, DNS, timeout): desligado.
            snapshot = Snapshot(OFFLINE, None, str(exc) or type(exc).__name__, time.time())
        else:
            if self.config.provider == "aternos":
                state = classify(status, self.config.offline_hints, self.config.transition_hints)
            else:
                state = ONLINE
            snapshot = Snapshot(state, status, None, time.time())
        self.snapshot = snapshot
        return snapshot
