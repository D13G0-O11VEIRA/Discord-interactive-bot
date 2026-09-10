import discord
from discord.ext import commands
from discord import app_commands

class Fun(commands.Cog): # Create a cog for fun commands
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command( # Define a slash command for showing a user's avatar
        name="avatar",
        description="Mostra o avatar de um usuário"
    )
    async def avatar(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member = None
    ):

        usuario = usuario or interaction.user

        embed = discord.Embed(
            title=f"Avatar de {usuario.display_name}",
            color=discord.Color.purple()
        )

        embed.set_image(url=usuario.display_avatar.url)

        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(Fun(bot))