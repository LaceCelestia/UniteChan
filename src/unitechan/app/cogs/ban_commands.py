from __future__ import annotations

from typing import List

import discord
from discord import app_commands
from discord.ext import commands

from unitechan.core.config_store import get_store
from unitechan.core.split_service import SplitService
from unitechan.core.lobby_store import LobbyStore
from unitechan.app.cogs._utils import is_admin


class BanCommands(commands.Cog):
    """ポケモンバン管理 Cog"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._service = SplitService(LobbyStore())

    ban = app_commands.Group(name='ban', description='バンするポケモンを管理します')

    # ---- Autocomplete ----

    async def _all_pokemon_ac(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        names = self._service.get_all_pokemon_names()
        return [
            app_commands.Choice(name=n, value=n)
            for n in names if current in n
        ][:25]

    async def _banned_pokemon_ac(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        banned = get_store().get_banned_pokemon(interaction.guild.id)
        return [
            app_commands.Choice(name=n, value=n)
            for n in sorted(banned) if current in n
        ][:25]

    # ---- コマンド ----

    @ban.command(name='add', description='ポケモンをバンします（管理者専用）')
    @app_commands.describe(pokemon='バンするポケモン名')
    @app_commands.autocomplete(pokemon=_all_pokemon_ac)
    async def ban_add(self, interaction: discord.Interaction, pokemon: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
            return

        ok = get_store().ban_pokemon(interaction.guild.id, pokemon)
        if not ok:
            await interaction.response.send_message(f'**{pokemon}** はすでにバン済みです。', ephemeral=True)
            return
        await interaction.response.send_message(f'🚫 **{pokemon}** をバンしました。', ephemeral=True)

    @ban.command(name='remove', description='バンを解除します（管理者専用）')
    @app_commands.describe(pokemon='バンを解除するポケモン名')
    @app_commands.autocomplete(pokemon=_banned_pokemon_ac)
    async def ban_remove(self, interaction: discord.Interaction, pokemon: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
            return

        ok = get_store().unban_pokemon(interaction.guild.id, pokemon)
        if not ok:
            await interaction.response.send_message(f'**{pokemon}** はバンされていません。', ephemeral=True)
            return
        await interaction.response.send_message(f'✅ **{pokemon}** のバンを解除しました。', ephemeral=True)

    @ban.command(name='list', description='バン中のポケモン一覧を表示します')
    async def ban_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return

        banned = get_store().get_banned_pokemon(interaction.guild.id)
        if not banned:
            await interaction.response.send_message('バン中のポケモンはいません。', ephemeral=True)
            return

        lines = '\n'.join(f'- {n}' for n in sorted(banned))
        await interaction.response.send_message(
            f'**バン中のポケモン ({len(banned)}件)**\n{lines}',
            ephemeral=True,
        )

    @ban.command(name='clear', description='バンをすべて解除します（管理者専用）')
    async def ban_clear(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
            return

        count = get_store().clear_banned_pokemon(interaction.guild.id)
        if count == 0:
            await interaction.response.send_message('バン中のポケモンはいません。', ephemeral=True)
            return
        await interaction.response.send_message(f'✅ バンを {count}件 すべて解除しました。', ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BanCommands(bot))
