"""Testes do módulo de Minecraft. Rodar na raiz do projeto:

    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.minecraft import slp
from services.minecraft.config import MinecraftConfig, split_address
from services.minecraft.controllers import AternosLinkController, LocalProcessController
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
    pretty_advancement,
    tellraw_command,
)
from services.minecraft.logparser import parse_line
from services.minecraft.monitor import OFFLINE, ONLINE, TRANSITION, ServerMonitor, classify
from services.minecraft.rcon import RconAuthError, RconClient, RconError
from services.minecraft.store import JsonStore
from services.minecraft.transports import LocalTransport, parse_plugin_message
from tests.fakes import FakeRconServer, FakeStatusServer

STATUS_PAYLOAD = {
    "version": {"name": "Paper 1.21.4", "protocol": 769},
    "players": {"max": 20, "online": 2, "sample": [{"name": "Steve", "id": "x"}, {"name": "Alex", "id": "y"}]},
    "description": {"text": "§aOlá ", "extra": [{"text": "mundo"}]},
}


class SlpTests(unittest.IsolatedAsyncioTestCase):
    async def test_ping_parses_status(self):
        async with FakeStatusServer(STATUS_PAYLOAD) as srv:
            status = await slp.ping("127.0.0.1", srv.port)
        self.assertEqual(status.version_name, "Paper 1.21.4")
        self.assertEqual((status.players_online, status.players_max), (2, 20))
        self.assertEqual(status.player_names, ["Steve", "Alex"])
        self.assertEqual(status.motd, "Olá mundo")  # cores removidas, 'extra' concatenado
        self.assertIsNotNone(status.latency_ms)

    async def test_ping_without_pong_still_returns_status(self):
        async with FakeStatusServer(STATUS_PAYLOAD, answer_ping=False) as srv:
            status = await slp.ping("127.0.0.1", srv.port)
        self.assertIsNone(status.latency_ms)
        self.assertEqual(status.players_online, 2)

    async def test_string_description(self):
        payload = {**STATUS_PAYLOAD, "description": "Servidor §lTop"}
        async with FakeStatusServer(payload) as srv:
            status = await slp.ping("127.0.0.1", srv.port)
        self.assertEqual(status.motd, "Servidor Top")

    async def test_closed_port_raises(self):
        async with FakeStatusServer(STATUS_PAYLOAD) as srv:
            port = srv.port
        with self.assertRaises(OSError):
            await slp.ping("127.0.0.1", port, timeout=2)


class RconTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_roundtrip(self):
        async with FakeRconServer("segredo", {"list": ["There are 0 of a max of 20 players online:"]}) as srv:
            client = RconClient("127.0.0.1", srv.port, "segredo")
            self.assertIn("0 of a max", await client.command("list"))
            await client.close()

    async def test_fragmented_response_is_joined(self):
        async with FakeRconServer("segredo", {"big": ["parte1-", "parte2-", "parte3"]}) as srv:
            client = RconClient("127.0.0.1", srv.port, "segredo")
            self.assertEqual(await client.command("big"), "parte1-parte2-parte3")
            await client.close()

    async def test_wrong_password(self):
        async with FakeRconServer("segredo") as srv:
            client = RconClient("127.0.0.1", srv.port, "errada")
            with self.assertRaises(RconAuthError):
                await client.command("list")

    async def test_connection_refused_becomes_rcon_error(self):
        async with FakeRconServer("x") as srv:
            port = srv.port
        client = RconClient("127.0.0.1", port, "x", timeout=1)
        with self.assertRaises(RconError):
            await client.command("list")

    async def test_reconnects_after_drop(self):
        async with FakeRconServer("segredo") as srv:
            client = RconClient("127.0.0.1", srv.port, "segredo")
            await client.command("a")
            client._writer.close()  # simula a conexão caindo
            await client.command("b")
            self.assertEqual(srv.received, ["a", "b"])
            self.assertEqual(srv.connections, 2)
            await client.close()


class LogParserTests(unittest.TestCase):
    def test_chat_formats(self):
        for line in (
            "[12:34:56] [Server thread/INFO]: <Steve> oi pessoal",
            "[12:34:56 INFO]: <Steve> oi pessoal",
            "[12:34:56] [Async Chat Thread - #0/INFO]: <Steve> oi pessoal",
            "[12:34:56] [Server thread/INFO] [net.minecraft.server.dedicated.DedicatedServer/]: <Steve> oi pessoal",
            "[12:34:56] [Server thread/INFO]: [Not Secure] <Steve> oi pessoal",
        ):
            self.assertEqual(parse_line(line), McEvent(CHAT, "Steve", "oi pessoal"), line)

    def test_join_leave(self):
        self.assertEqual(parse_line("[12:00:00] [Server thread/INFO]: Steve joined the game"), McEvent(JOIN, "Steve"))
        self.assertEqual(parse_line("[12:00:00] [Server thread/INFO]: Steve left the game"), McEvent(LEAVE, "Steve"))

    def test_advancement(self):
        event = parse_line("[12:00:00] [Server thread/INFO]: Steve has made the advancement [Stone Age]")
        self.assertEqual(event, McEvent(ADVANCEMENT, "Steve", "Stone Age"))
        event = parse_line("[12:00:00] [Server thread/INFO]: Steve has completed the challenge [Monster Hunter]")
        self.assertEqual(event.kind, ADVANCEMENT)

    def test_deaths(self):
        for msg in (
            "Steve was slain by Zombie",
            "Steve fell from a high place",
            "Steve drowned",
            "Steve tried to swim in lava",
            "Steve was shot by Skeleton",
        ):
            event = parse_line(f"[12:00:00] [Server thread/INFO]: {msg}")
            self.assertEqual(event, McEvent(DEATH, "Steve", msg), msg)

    def test_server_lifecycle(self):
        done = parse_line('[12:00:00] [Server thread/INFO]: Done (12.345s)! For help, type "help"')
        self.assertEqual(done, McEvent(SERVER_STARTED))
        self.assertEqual(parse_line("[12:00:00] [Server thread/INFO]: Stopping server"), McEvent(SERVER_STOPPING))

    def test_ignores_noise(self):
        for line in (
            "",
            "random text",
            "[12:00:00] [Server thread/INFO]: Preparing spawn area: 42%",
            "[12:00:00] [Server thread/INFO]: Steve lost connection: Disconnected",
            "[12:00:00] [Server thread/INFO]: Steve issued server command: /gamemode creative",
        ):
            self.assertIsNone(parse_line(line), line)


class EventTextTests(unittest.TestCase):
    def test_clean_blocks_color_codes_and_newlines(self):
        self.assertEqual(clean_for_minecraft("§4oi\nmundo\t!"), "4oi mundo !")
        self.assertEqual(clean_for_minecraft("<:kekw:123456789>"), ":kekw:")

    def test_clean_truncates(self):
        out = clean_for_minecraft("a" * 1000, 50)
        self.assertEqual(len(out), 50)
        self.assertTrue(out.endswith("…"))

    def test_tellraw_is_valid_json_and_escapes_quotes(self):
        import json

        command = tellraw_command('Fulano"} ,{"text":"hack', 'oi "mundo" ✓')
        self.assertTrue(command.startswith("tellraw @a "))
        payload = json.loads(command[len("tellraw @a "):])
        self.assertEqual(len(payload), 3)  # a tentativa de injeção não criou um quarto componente
        self.assertIn('oi "mundo" ✓', payload[2]["text"])
        self.assertTrue(command.isascii())

    def test_pretty_advancement(self):
        self.assertEqual(pretty_advancement("adventure/kill_a_mob"), "Kill A Mob")


class PluginMessageTests(unittest.TestCase):
    def test_chat(self):
        event = parse_plugin_message({"type": "chat", "player": "Steve", "message": " oi "})
        self.assertEqual(event, McEvent(CHAT, "Steve", "oi"))

    def test_rejects_garbage(self):
        for data in (None, [], "x", {}, {"type": "chat"}, {"type": "chat", "player": "x"}, {"type": "wat"}):
            self.assertIsNone(parse_plugin_message(data), data)

    def test_advancement_filters_recipes_and_root(self):
        self.assertIsNone(parse_plugin_message({"type": "advancement", "player": "S", "advancement": "recipes/x/y"}))
        self.assertIsNone(parse_plugin_message({"type": "advancement", "player": "S", "advancement": "story/root"}))
        event = parse_plugin_message({"type": "advancement", "player": "S", "advancement": "story/mine_stone"})
        self.assertEqual(event, McEvent(ADVANCEMENT, "S", "Mine Stone"))

    def test_death_and_lifecycle(self):
        self.assertEqual(
            parse_plugin_message({"type": "death", "player": "S", "message": "S fell"}), McEvent(DEATH, "S", "S fell")
        )
        self.assertEqual(parse_plugin_message({"type": "server_started"}), McEvent(SERVER_STARTED))


class LocalTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "latest.log"
        self.addCleanup(self.tmp.cleanup)

    def _transport(self, rcon=None):
        return LocalTransport(self.log, rcon or RconClient("127.0.0.1", 1, "x"))

    def test_tail_skips_old_content_then_reads_new_lines(self):
        self.log.write_text("[10:00:00] [Server thread/INFO]: <Old> antigo\n", encoding="utf-8")
        transport = self._transport()
        self.assertEqual(transport.read_new_lines(), [])  # primeira leitura só marca a posição
        with open(self.log, "a", encoding="utf-8") as handle:
            handle.write("[10:00:01] [Server thread/INFO]: <Steve> novo\n")
        self.assertEqual(transport.read_new_lines(), ["[10:00:01] [Server thread/INFO]: <Steve> novo"])
        self.assertEqual(transport.read_new_lines(), [])

    def test_partial_line_waits_for_newline(self):
        self.log.write_text("", encoding="utf-8")
        transport = self._transport()
        transport.read_new_lines()
        with open(self.log, "a", encoding="utf-8") as handle:
            handle.write("[10:00:01] [Server thread/INFO]: <Steve> meta")
        self.assertEqual(transport.read_new_lines(), [])
        with open(self.log, "a", encoding="utf-8") as handle:
            handle.write("de\n")
        self.assertEqual(transport.read_new_lines(), ["[10:00:01] [Server thread/INFO]: <Steve> metade"])

    def test_log_rotation_restarts_from_beginning(self):
        self.log.write_text("linha antiga bem comprida para ficar maior que a nova\n", encoding="utf-8")
        transport = self._transport()
        transport.read_new_lines()
        self.log.unlink()
        self.log.write_text("nova\n", encoding="utf-8")  # o servidor reiniciou e recriou o arquivo
        self.assertEqual(transport.read_new_lines(), ["nova"])

    def test_utf8_is_preserved(self):
        self.log.write_text("", encoding="utf-8")
        transport = self._transport()
        transport.read_new_lines()
        with open(self.log, "a", encoding="utf-8") as handle:
            handle.write("[10:00:01] [Server thread/INFO]: <Zé> olá ação\n")
        self.assertEqual(transport.read_new_lines()[0].split("> ", 1)[1], "olá ação")

    async def test_end_to_end_events_and_chat_to_game(self):
        async with FakeRconServer("pw", {"list": ["There are 1 of a max of 20 players online: Steve"]}) as srv:
            transport = self._transport(RconClient("127.0.0.1", srv.port, "pw"))
            transport.poll_interval = 0.05
            self.log.write_text("", encoding="utf-8")
            events: list[McEvent] = []

            async def handler(event):
                events.append(event)

            transport.set_handler(handler)
            await transport.start()
            await asyncio.sleep(0.15)
            with open(self.log, "a", encoding="utf-8") as handle:
                handle.write("[10:00:01] [Server thread/INFO]: Steve joined the game\n")
                handle.write("[10:00:02] [Server thread/INFO]: <Steve> oi discord\n")
            for _ in range(40):
                if len(events) >= 2:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(events, [McEvent(JOIN, "Steve"), McEvent(CHAT, "Steve", "oi discord")])
            self.assertEqual(transport.players, ["Steve"])

            self.assertTrue(await transport.send_chat("Maria", "olá, jogo"))
            self.assertTrue(srv.received[-1].startswith("tellraw @a "))
            self.assertEqual(await transport.list_players(), ["Steve"])
            await transport.stop()

    async def test_send_chat_returns_false_when_rcon_down(self):
        transport = self._transport(RconClient("127.0.0.1", 1, "x", timeout=0.5))
        self.assertFalse(await transport.send_chat("a", "b"))


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    def _config(self, port: int, provider: str = "aternos") -> MinecraftConfig:
        env = {"MC_ADDRESS": f"127.0.0.1:{port}", "MC_PROVIDER": provider}
        if provider == "local":
            env["MC_START_COMMAND"] = "x"
        with mock.patch.dict(os.environ, env, clear=True):
            return MinecraftConfig.from_env()

    async def test_aternos_placeholder_is_offline(self):
        payload = {"version": {"name": "● Offline", "protocol": 3}, "players": {"max": 0, "online": 0},
                   "description": "§7Este servidor está desligado"}
        async with FakeStatusServer(payload) as srv:
            snap = await ServerMonitor(self._config(srv.port)).refresh()
        self.assertEqual(snap.state, OFFLINE)

    async def test_aternos_starting_is_transition(self):
        payload = {"version": {"name": "● Starting", "protocol": 3}, "players": {"max": 0, "online": 0},
                   "description": "x"}
        async with FakeStatusServer(payload) as srv:
            snap = await ServerMonitor(self._config(srv.port)).refresh()
        self.assertEqual(snap.state, TRANSITION)

    async def test_real_server_is_online(self):
        async with FakeStatusServer(STATUS_PAYLOAD) as srv:
            snap = await ServerMonitor(self._config(srv.port)).refresh()
        self.assertEqual(snap.state, ONLINE)
        self.assertEqual(snap.status.players_online, 2)

    async def test_connection_refused_is_offline(self):
        async with FakeStatusServer(STATUS_PAYLOAD) as srv:
            port = srv.port
        snap = await ServerMonitor(self._config(port, "local")).refresh()
        self.assertEqual(snap.state, OFFLINE)
        self.assertIsNone(snap.status)
        self.assertTrue(snap.error)

    def test_classify_prefers_transition_over_offline(self):
        status = slp.ServerStatus("Offline Starting", 0, 0, 0, [], "", None)
        self.assertEqual(classify(status, ("offline",), ("starting",)), TRANSITION)


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_aternos_never_claims_to_start_it(self):
        result = await AternosLinkController("https://aternos.org/servers/").start()
        self.assertFalse(result.ok)
        self.assertTrue(result.manual)
        self.assertEqual(result.url, "https://aternos.org/servers/")

    async def test_local_start_spawns_process_in_server_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "started.txt"
            script = f"import pathlib; pathlib.Path('started.txt').write_text('ok')"
            command = f'"{sys.executable}" -c "{script}"'
            controller = LocalProcessController(command, Path(tmp), None)
            result = await controller.start()
            self.assertTrue(result.ok, result.message)
            for _ in range(50):
                if marker.exists():
                    break
                await asyncio.sleep(0.1)
            self.assertTrue(marker.exists())

    async def test_local_start_reports_bad_command(self):
        result = await LocalProcessController("/caminho/que/nao/existe", None, None).start()
        self.assertFalse(result.ok)

    async def test_local_stop_uses_rcon(self):
        async with FakeRconServer("pw") as srv:
            rcon = RconClient("127.0.0.1", srv.port, "pw")
            result = await LocalProcessController("x", None, rcon).stop()
            self.assertTrue(result.ok)
            self.assertEqual(srv.received, ["stop"])
            await rcon.close()


class ConfigAndStoreTests(unittest.TestCase):
    def test_split_address(self):
        self.assertEqual(split_address("a.aternos.me:61370"), ("a.aternos.me", 61370))
        self.assertEqual(split_address("play.exemplo.com"), ("play.exemplo.com", 25565))
        with self.assertRaises(ValueError):
            split_address("host:abc")

    def _load(self, **env):
        with mock.patch.dict(os.environ, env, clear=True):
            return MinecraftConfig.from_env()

    def test_requires_address(self):
        with self.assertRaises(ValueError):
            self._load()

    def test_defaults(self):
        cfg = self._load(MC_ADDRESS="a.aternos.me:61370")
        self.assertEqual((cfg.provider, cfg.bridge_mode, cfg.port), ("aternos", "off", 61370))
        self.assertEqual(cfg.address, "a.aternos.me:61370")

    def test_plugin_mode_demands_long_token(self):
        with self.assertRaises(ValueError):
            self._load(MC_ADDRESS="a:1", MC_BRIDGE_MODE="plugin", MC_WS_TOKEN="curto")
        cfg = self._load(MC_ADDRESS="a:1", MC_BRIDGE_MODE="plugin", MC_WS_TOKEN="x" * 20)
        self.assertEqual(cfg.bridge_mode, "plugin")

    def test_local_mode_needs_log_and_rcon(self):
        with self.assertRaises(ValueError):
            self._load(MC_ADDRESS="a:1", MC_BRIDGE_MODE="local")
        cfg = self._load(MC_ADDRESS="a:1", MC_BRIDGE_MODE="local", MC_SERVER_DIR="/srv/mc", MC_RCON_PASSWORD="pw")
        self.assertEqual(cfg.log_file, Path("/srv/mc/logs/latest.log"))

    def test_bad_values_are_rejected(self):
        with self.assertRaises(ValueError):
            self._load(MC_ADDRESS="a:1", MC_PROVIDER="outro")
        with self.assertRaises(ValueError):
            self._load(MC_ADDRESS="a:1", MC_BRIDGE_CHANNEL_ID="abc")

    def test_store_roundtrip_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "mc.json"
            store = JsonStore(path)
            store.set("bridge_channel_id", 123)
            self.assertEqual(JsonStore(path).get("bridge_channel_id"), 123)
            store.set("bridge_channel_id", None)
            self.assertIsNone(JsonStore(path).get("bridge_channel_id"))

    def test_store_survives_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mc.json"
            path.write_text("{ isso não é json", encoding="utf-8")
            self.assertIsNone(JsonStore(path).get("x"))


if __name__ == "__main__":
    unittest.main()
