import discord
from discord.ext import commands

COR_PADRAO = discord.Color.purple()


class Welcome(commands.Cog):
    """Cog responsável pelas boas-vindas."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:

        canal = member.guild.system_channel

        if canal is None:
            return

        embed = discord.Embed(
            title="🎉 Bem-vindo!",
            description=(
                f"Olá {member.mention}, seja muito bem-vindo(a) "
                f"ao **{member.guild.name}**!"
            ),
            color=COR_PADRAO
        )

        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(
            name="Membro nº",
            value=str(member.guild.member_count),
            inline=True
        )

        embed.set_footer(
            text="Esperamos que aproveite a comunidade!"
        )

        await canal.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))