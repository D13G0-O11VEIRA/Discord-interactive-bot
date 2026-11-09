"""Armazenamento simples em JSON para configurações definidas por comando (/mc configurar)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("DiscordBot.minecraft.store")


class JsonStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._data = {}
        except (OSError, json.JSONDecodeError):
            logger.exception("Não consegui ler %s; começando vazio", self.path)
            self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if value is None:
            self._data.pop(key, None)
        else:
            self._data[key] = value
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)  # troca atômica: nunca deixa o arquivo pela metade
