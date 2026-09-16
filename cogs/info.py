import discord
from discord.ext import commands
from discord import app_commands

COR_PADRAO = discord.Color.purple()

class Info(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
    name="painel",
    description="Exibe um painel interativo."
    )
    async def painel(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="🎮 Painel Interativo",
            description="Clique no botão abaixo para testar a interação.",
            color=COR_PADRAO
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
    description="Mostra informações do servidor."
    )
    async def server(self, interaction: discord.Interaction) -> None:
        servidor = interaction.guild
        if servidor is None:
            await interaction.response.send_message(
                "Este comando só funciona em servidores.",
                ephemeral=True
            )
            return
        embed = discord.Embed(
            title=f"📌 {servidor.name}",
            color=COR_PADRAO
        )
        embed.add_field(
            name="👥 Membros",
            value=f"{servidor.member_count}",
            inline=True
        )
        embed.add_field(
            name="📅 Criado em",
            value=discord.utils.format_dt(
                servidor.created_at,
                style="D"
            ),
            inline=True
        )
        if servidor.icon:
            embed.set_thumbnail(url=servidor.icon.url)
        embed.set_footer(text=f"ID: {servidor.id}")
        await interaction.response.send_message(embed=embed)

class PainelView(discord.ui.View):
    """Painel com botão interativo."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Clique aqui",
        emoji="👋",
        style=discord.ButtonStyle.green
    )
    async def botao(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ) -> None:

        await interaction.response.send_message(
            "Você apertou o botão! 👋",
            ephemeral=True
        )


class CorSelect(discord.ui.Select):
    """Menu suspenso de cores."""

    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label="Roxo",
                emoji="💜",
                description="Escolha a cor roxa"
            ),
            discord.SelectOption(
                label="Azul",
                emoji="💙",
                description="Escolha a cor azul"
            ),
            discord.SelectOption(
                label="Verde",
                emoji="💚",
                description="Escolha a cor verde"
            )
        ]
        super().__init__(
            placeholder="Escolha uma cor...",
            min_values=1,
            max_values=1,
            options=options
        )
    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            f"Você escolheu **{self.values[0]}**.",
            ephemeral=True
        )


class MenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CorSelect())


async def setup(bot):
    await bot.add_cog(Info(bot))