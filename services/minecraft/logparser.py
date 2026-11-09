"""Converte linhas do latest.log do servidor em eventos (chat, entrada, saída, morte...).

Funciona com os formatos de log do vanilla, Paper/Spigot e Forge/Fabric:

    [12:34:56] [Server thread/INFO]: <Steve> oi
    [12:34:56 INFO]: <Steve> oi
    [12:34:56] [Server thread/INFO] [net.minecraft.server.dedicated.DedicatedServer/]: <Steve> oi

Mortes são detectadas por frases conhecidas (heurística); o plugin da pasta
companion/ envia a mensagem exata de morte, sem adivinhação.
"""

from __future__ import annotations

import re

from services.minecraft.events import (
    ADVANCEMENT,
    CHAT,
    DEATH,
    JOIN,
    LEAVE,
    SERVER_STARTED,
    SERVER_STOPPING,
    McEvent,
)

# "[HH:MM:SS ...]" + qualquer quantidade de "[...]" + ":" + mensagem
_LINE_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}[^\]]*\]\s*(?:\[[^\]]*\]\s*)*:\s?(?P<msg>.*)$")

_CHAT_RE = re.compile(r"^(?:\[Not Secure\]\s*)?<(?P<name>[^>]+)>\s(?P<text>.+)$")
_JOIN_RE = re.compile(r"^(?P<name>\S+) joined the game$")
_LEAVE_RE = re.compile(r"^(?P<name>\S+) left the game$")
_ADVANCEMENT_RE = re.compile(
    r"^(?P<name>\S+) has (?:made the advancement|completed the challenge|reached the goal) \[(?P<adv>.+)\]$"
)
_DONE_RE = re.compile(r"^Done \([\d.,]+s\)! For help, type \"help\"")

_DEATH_PHRASES = (
    r"was (?:slain|shot|fireballed|killed|pummeled|pricked|squished|squashed|impaled|blown up|struck|burned|"
    r"doomed|stung|skewered|obliterated|poked|roasted|knocked|frozen|speared|bitten|sniped|zapped)",
    r"fell (?:from|off|out of|while|into)",
    r"drowned",
    r"burned to death",
    r"blew up",
    r"hit the ground too hard",
    r"starved to death",
    r"suffocated in a wall",
    r"tried to swim in lava",
    r"died",
    r"went up in flames",
    r"walked into (?:fire|a cactus|danger)",
    r"withered away",
    r"experienced kinetic energy",
    r"froze to death",
    r"discovered the floor was lava",
    r"left the confines of this world",
    r"didn't want to live in the same world",
    r"was pricked to death",
    r"got finished off",
    r"fell out of the world",
)
_DEATH_RE = re.compile(r"^(?P<name>\S+) (?:" + "|".join(_DEATH_PHRASES) + r")\b")


def parse_line(line: str) -> McEvent | None:
    """Devolve o evento correspondente à linha do log ou None se ela não interessa."""
    match = _LINE_RE.match(line.rstrip("\r\n"))
    if not match:
        return None
    msg = match.group("msg")

    if chat := _CHAT_RE.match(msg):
        return McEvent(CHAT, chat.group("name"), chat.group("text"))
    if join := _JOIN_RE.match(msg):
        return McEvent(JOIN, join.group("name"))
    if leave := _LEAVE_RE.match(msg):
        return McEvent(LEAVE, leave.group("name"))
    if adv := _ADVANCEMENT_RE.match(msg):
        return McEvent(ADVANCEMENT, adv.group("name"), adv.group("adv"))
    if _DONE_RE.match(msg):
        return McEvent(SERVER_STARTED)
    if msg == "Stopping server":
        return McEvent(SERVER_STOPPING)
    if death := _DEATH_RE.match(msg):
        return McEvent(DEATH, death.group("name"), msg)
    return None
