import discord
from discord.ext import commands
from services.gemini import GeminiService

class AI(commands.Cog):
    """Cog responsável pelas funcionalidades de inteligência artificial."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.gemini = GeminiService()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Processa mensagens que mencionam a Iki."""

        if message.author.bot:                               # Ignora mensagens enviadas por bots
            return
        if self.bot.user is None:                            # Garante que o bot está disponível
            return
        if self.bot.user not in message.mentions:            # Só responde quando a Iki é mencionada
            return
        texto = self._remover_mencao(message)
        if not texto:                                        # Caso o usuário apenas mencione a Iki sem escrever nada
            await message.reply(
                "Oi! Você queria falar comigo? ¬_¬"
            )
            return
        try:                                                 # Tenta gerar uma resposta usando o Gemini
            async with message.channel.typing():
                resposta = await self.gemini.responder(texto)
            await message.reply(resposta)
        except Exception:
            await message.reply(
                "Desculpa, tive um probleminha para pensar nessa resposta. (╯°□°）╯︵ ┻━┻"
            )

    def _remover_mencao(self, message: discord.Message) -> str:
        """Remove a menção da Iki da mensagem."""

        if self.bot.user is None:
            return message.content
        texto = message.content
        texto = texto.replace(
            f"<@{self.bot.user.id}>",
            ""
        )
        texto = texto.replace(
            f"<@!{self.bot.user.id}>",
            ""
        )
        return texto.strip()

async def setup(bot: commands.Bot) -> None:
    """Carrega o Cog de inteligência artificial."""
    await bot.add_cog(AI(bot))