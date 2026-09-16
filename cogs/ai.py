import discord
from discord.ext import commands
from services.gemini import GeminiService

class AI(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.gemini = GeminiService()

    @commands.Cog.listener()
    async def on_message(self, message):

        if message.author.bot:                          # Ignora mensagens de outros bots evitando loops infinitos
            return
        if self.bot.user not in message.mentions:       # Ignora mensagens que não mencionam o bot
            return
        texto = message.content.replace(                # Remove a menção do bot da mensagem para o processamento
            f"<@{self.bot.user.id}>",
            ""
        ).replace(
            f"<@!{self.bot.user.id}>",
            ""
        ).strip()
        if not texto:                                   # Ignora mensagens vazias
            return
        async with message.channel.typing():            # Mostra que o bot está digitando
            resposta = self.gemini.responder(texto)
        await message.reply(resposta)

async def setup(bot):
    await bot.add_cog(AI(bot))