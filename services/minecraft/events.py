"""Eventos do jogo e utilitários de texto usados pela ponte de chat."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

CHAT = "chat"
JOIN = "join"
LEAVE = "leave"
DEATH = "death"
ADVANCEMENT = "advancement"
SERVER_STARTED = "server_started"
SERVER_STOPPING = "server_stopping"

MAX_CHAT_LENGTH = 256


@dataclass(frozen=True)
class McEvent:
    """Algo que aconteceu no servidor de Minecraft."""

    kind: str
    player: str = ""
    text: str = ""


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f§]")
_SPACES_RE = re.compile(r"\s+")
_CUSTOM_EMOJI_RE = re.compile(r"<a?:(\w+):\d+>")


def clean_for_minecraft(text: str, limit: int = MAX_CHAT_LENGTH) -> str:
    """Prepara um texto do Discord para ir ao chat do jogo.

    Remove quebras de linha, caracteres de controle e o '§' (que permitiria
    injetar códigos de cor), troca emojis customizados por ':nome:' e limita o tamanho.
    """
    text = _CUSTOM_EMOJI_RE.sub(r":\1:", text)
    text = _CONTROL_RE.sub(" ", text)
    text = _SPACES_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def tellraw_command(author: str, text: str) -> str:
    """Monta o comando /tellraw usado quando o envio é feito por RCON."""
    payload = [
        {"text": "[Discord] ", "color": "blue"},
        {"text": clean_for_minecraft(author, 32), "color": "white"},
        {"text": f": {clean_for_minecraft(text)}", "color": "gray"},
    ]
    # ensure_ascii=True mantém o comando 100% ASCII (acentos e emojis viram \uXXXX).
    return "tellraw @a " + json.dumps(payload, ensure_ascii=True)


def pretty_advancement(key: str) -> str:
    """'adventure/kill_a_mob' -> 'Kill A Mob' (o plugin envia a chave interna)."""
    last = key.rsplit("/", 1)[-1].replace("_", " ").strip()
    return last.title() if last else key
