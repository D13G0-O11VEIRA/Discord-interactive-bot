import discord
from discord.ext import commands

class Welcome(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member):

        canal = member.guild.system_channel

        if canal:
            embed = discord.Embed(
                title="🎉 Bem-vindo!",
                description=f"Olá {member.mention}, seja bem-vindo ao servidor!",
                color=discord.Color.purple()
            )

            embed.set_thumbnail(url=member.display_avatar.url)

            await canal.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Welcome(bot))