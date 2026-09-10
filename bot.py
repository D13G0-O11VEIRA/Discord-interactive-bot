import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

load_dotenv() # Load environment variables from the .env file

intents = discord.Intents.default() # Create an instance of Intents with default settings
intents.message_content = True # Allow the bot to read message content
intents.members = True # Allow the bot to access member information

class MeuBot(commands.Bot): # Create a custom bot class that allows for command handling and event management
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents
        )

    async def setup_hook(self):
        for arquivo in os.listdir("./cogs"):  # Loop through all files in the cogs directory
            if arquivo.endswith(".py"):
                nome = f"cogs.{arquivo[:-3]}"
                try:
                    await self.load_extension(nome)
                    print(f"✓ {arquivo} carregado")
                except Exception as e:
                    print(f"✗ Erro em {arquivo}: {e}")

        # Sincroniza os comandos globalmente
        comandos = await self.tree.sync()

        print("Comandos sincronizados!")
        print(f"Total: {len(comandos)} comandos")

        for comando in comandos:
            print(f" - /{comando.name}")

bot = MeuBot() # Create an instance of the bot

@bot.event # Event handler for when the bot is ready
async def on_ready():
    print(f"{bot.user} está online!")


@bot.tree.command(name="ping", description="Mostra a latência do bot")
async def ping(interaction: discord.Interaction):

    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Latência: **{round(bot.latency*1000)}ms**",
        color=discord.Color.purple()
    )

    await interaction.response.send_message(embed=embed)

bot.run(os.getenv("DISCORD_TOKEN")) # Run the bot using the token from the environment variable