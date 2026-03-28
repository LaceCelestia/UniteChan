from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from unitechan.core.config_store import get_store
from unitechan.app.cogs._utils import is_admin


class SeparateCommands(commands.Cog):
    """チーム分け分離ペア管理 Cog"""

    separate = app_commands.Group(name='separate', description='必ず別チームにするペアを管理します')

    @separate.command(name='add', description='指定した2人を必ず別チームにします（管理者専用）')
    @app_commands.describe(member1='1人目', member2='2人目')
    async def separate_add(
        self,
        interaction: discord.Interaction,
        member1: discord.Member,
        member2: discord.Member,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
            return
        if member1.id == member2.id:
            await interaction.response.send_message('同じメンバーは指定できません。', ephemeral=True)
            return

        ok = get_store().add_separate_pair(interaction.guild.id, member1.id, member2.id)
        if not ok:
            await interaction.response.send_message(
                f'{member1.display_name} と {member2.display_name} はすでに登録されています。',
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f'✅ **{member1.display_name}** と **{member2.display_name}** を別チームペアに登録しました。',
        )

    @separate.command(name='remove', description='別チームペアの登録を解除します（管理者専用）')
    @app_commands.describe(member1='1人目', member2='2人目')
    async def separate_remove(
        self,
        interaction: discord.Interaction,
        member1: discord.Member,
        member2: discord.Member,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
            return

        ok = get_store().remove_separate_pair(interaction.guild.id, member1.id, member2.id)
        if not ok:
            await interaction.response.send_message(
                f'{member1.display_name} と {member2.display_name} のペアは登録されていません。',
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f'✅ **{member1.display_name}** と **{member2.display_name}** のペアを解除しました。',
        )

    @separate.command(name='list', description='登録中の別チームペア一覧を表示します')
    async def separate_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return

        pairs = get_store().get_separate_pairs(interaction.guild.id)
        if not pairs:
            await interaction.response.send_message('別チームペアは登録されていません。', ephemeral=True)
            return

        lines = []
        for uid1, uid2 in pairs:
            m1 = interaction.guild.get_member(uid1)
            m2 = interaction.guild.get_member(uid2)
            n1 = m1.display_name if m1 else f'<@{uid1}>'
            n2 = m2.display_name if m2 else f'<@{uid2}>'
            lines.append(f'・{n1}  ↔  {n2}')

        await interaction.response.send_message(
            f'**別チームペア一覧（{len(pairs)}件）**\n' + '\n'.join(lines),
        )

    @separate.command(name='clear', description='別チームペアをすべて解除します（管理者専用）')
    async def separate_clear(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
            return

        count = get_store().clear_separate_pairs(interaction.guild.id)
        if count == 0:
            await interaction.response.send_message('解除するペアがありません。', ephemeral=True)
            return

        await interaction.response.send_message(
            f'✅ 別チームペアを {count}件 すべて解除しました。',
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SeparateCommands(bot))
