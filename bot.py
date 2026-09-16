import logging
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Configuração inicial
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("DiscordBot")    # Cria um logger para o bot
intents = discord.Intents.default()         # Permite que o bot receba eventos do Discord
intents.message_content = True              # Permite que o bot leia o conteúdo das mensagens
intents.members = True                      # Permite que o bot receba eventos de membros (como entrada e saída do servidor)

# Classe principal do Bot
class MeuBot(commands.Bot):
    """Classe principal do bot."""

    def __init__(self) -> None:
        super().__init__(
            command_prefix="!",
            intents=intents
        )

    async def setup_hook(self) -> None:
        """Carrega automaticamente todos os Cogs."""

        for arquivo in os.listdir("./cogs"): # Carrega todos os arquivos na pasta cogs
            if not arquivo.endswith(".py"):
                continue
            if arquivo.startswith("__"):
                continue
            nome = f"cogs.{arquivo[:-3]}"
            try:
                await self.load_extension(nome)
                logger.info("✓ %s carregado", arquivo)
            except Exception:
                logger.exception("Erro ao carregar %s", arquivo)

        comandos = await self.tree.sync() # Sincroniza os comandos de barra (/) com o Discord
        logger.info("Comandos sincronizados (%s)", len(comandos))

        for comando in comandos: # Loga os comandos sincronizados
            logger.info(" - /%s", comando.name)

bot = MeuBot()

# Evento disparado quando o bot está pronto
@bot.event 
async def on_ready() -> None:
    logger.info("%s está online!", bot.user)

# Comandos globais
@bot.tree.command(
    name="ping",
    description="Mostra a latência do bot."
)
async def ping(interaction: discord.Interaction) -> None:

    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Latência: **{round(bot.latency * 1000)} ms**",
        color=discord.Color.purple()
    )
    await interaction.response.send_message(embed=embed)

# Inicialização
bot.run(os.getenv("DISCORD_TOKEN"))