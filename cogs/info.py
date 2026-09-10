import discord
from discord.ext import commands
from discord import app_commands


class Info(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="painel",
        description="Mostra um painel com botão"
    )
    async def painel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Painel Interativo",
            description="Clique no botão abaixo.",
            color=discord.Color.purple()
        )

        await interaction.response.send_message(
            embed=embed,
            view=PainelView()
        )

    @app_commands.command(
        name="menu",
        description="Mostra um menu suspenso"
    )
    async def menu(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Escolha uma opção:",
            view=MenuView()
        )

    @app_commands.command(
        name="server",
        description="Mostra informações do servidor"
    )
    async def server(self, interaction: discord.Interaction):
        servidor = interaction.guild

        embed = discord.Embed(
            title=f"{servidor.name}",
            color=discord.Color.purple()
        )

        embed.add_field(
            name="👥 Membros",
            value=str(servidor.member_count),
            inline=True
        )

        embed.add_field(
            name="📅 Criado em",
            value=servidor.created_at.strftime("%d/%m/%Y"),
            inline=True
        )

        if servidor.icon:
            embed.set_thumbnail(url=servidor.icon.url)

        await interaction.response.send_message(embed=embed)


class PainelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Clique aqui",
        style=discord.ButtonStyle.green,
        emoji="👋"
    )
    async def botao(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await interaction.response.send_message(
            "Você apertou o botão!",
            ephemeral=True
        )


class CorSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Roxo", emoji="💜"),
            discord.SelectOption(label="Azul", emoji="💙"),
            discord.SelectOption(label="Verde", emoji="💚")
        ]

        super().__init__(
            placeholder="Escolha uma cor",
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Você escolheu {self.values[0]}",
            ephemeral=True
        )


class MenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CorSelect())


async def setup(bot):
    await bot.add_cog(Info(bot))