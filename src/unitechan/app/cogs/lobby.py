from typing import Dict, Set

import discord
from discord import app_commands
from discord.ext import commands

from unitechan.core.lobby_store import LobbyStore


class Lobby(commands.Cog):
    '''ユナイト用のロビー管理 Cog'''

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = LobbyStore()

    # ---- コマンド ----

    @app_commands.command(name='join', description='ユナイト用ロビーに参加します')
    async def join(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                'サーバー内で使ってね。', ephemeral=True,
            )
            return

        if interaction.user.bot:
            await interaction.response.send_message(
                'Bot はロビーに参加できないよ。', ephemeral=True,
            )
            return

        guild_id = interaction.guild.id
        lobby = self.store.get_lobby(guild_id)

        if interaction.user.id in lobby:
            await interaction.response.send_message(
                f'すでにロビーに参加しています。\n現在人数: **{len(lobby)}人**',
                ephemeral=True,
            )
            return

        size = self.store.join(guild_id, interaction.user.id)

        await interaction.response.send_message(
            f'ロビーに参加しました！\n現在人数: **{size}人**',
            ephemeral=True,
        )

    @app_commands.command(name='leave', description='ロビーから抜けます')
    async def leave(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                'サーバー内で使ってね。', ephemeral=True,
            )
            return

        guild_id = interaction.guild.id
        lobby = self.store.get_lobby(guild_id)

        if interaction.user.id not in lobby:
            await interaction.response.send_message(
                'ロビーに参加していません。', ephemeral=True,
            )
            return

        size = self.store.leave(guild_id, interaction.user.id)

        await interaction.response.send_message(
            f'ロビーから抜けました。\n現在人数: **{size}人**',
            ephemeral=True,
        )

    @app_commands.command(name='rank', description='自分のランクを登録・変更します（ポケモンユナイト）')
    @app_commands.choices(
        rank=[
            app_commands.Choice(name='ビギナー', value='ビギナー'),
            app_commands.Choice(name='スーパー', value='スーパー'),
            app_commands.Choice(name='ハイパー', value='ハイパー'),
            app_commands.Choice(name='エリート', value='エリート'),
            app_commands.Choice(name='エキスパート', value='エキスパート'),
            app_commands.Choice(name='マスター', value='マスター'),
            app_commands.Choice(name='レジェンド', value='レジェンド'),
        ],
    )
    async def rank(
        self,
        interaction: discord.Interaction,
        rank: app_commands.Choice[str],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                'サーバー内で使ってね。', ephemeral=True,
            )
            return

        guild_id = interaction.guild.id
        self.store.set_rank(guild_id, interaction.user.id, rank.value)

        await interaction.response.send_message(
            f'ランクを **{rank.name}** に設定しました。',
            ephemeral=True,
        )

    @app_commands.command(name='kick', description='ロビーからメンバーを追放します（管理者専用）')
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                'サーバー内で使ってね。', ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                'サーバー内で使ってね。', ephemeral=True,
            )
            return

        perms = interaction.user.guild_permissions
        if not (perms.administrator or perms.manage_guild or perms.manage_roles):
            await interaction.response.send_message(
                'このコマンドは管理者のみ使用できます。',
                ephemeral=True,
            )
            return

        guild_id = interaction.guild.id
        ok = self.store.kick(guild_id, member.id)

        if not ok:
            await interaction.response.send_message(
                'そのメンバーはロビーにいません。',
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f'{member.display_name} をロビーから削除しました。',
            ephemeral=True,
        )

    @app_commands.command(name='lobby', description='現在のロビーメンバーを表示します')
    async def lobby(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                'サーバー内で使ってね。', ephemeral=True,
            )
            return

        guild_id = interaction.guild.id
        lobby, ranks = self.store.snapshot(guild_id)

        if not lobby:
            await interaction.response.send_message(
                'ロビーには誰もいません。', ephemeral=True,
            )
            return

        members = []
        for uid in lobby:
            mention = f'<@{uid}>'
            rank_str = ranks.get(uid)
            if rank_str:
                members.append(f'- {mention} ({rank_str})')
            else:
                members.append(f'- {mention}')

        lines = '\n'.join(members)

        await interaction.response.send_message(
            f'**現在のロビー（{len(members)}人）**\n{lines}',
            ephemeral=True,
        )

    @app_commands.command(name='lobby_clear', description='ロビーを全員解散します（管理者専用）')
    async def lobby_clear(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                'サーバー内で使ってね。', ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                'サーバー内で使ってね。', ephemeral=True,
            )
            return

        perms = interaction.user.guild_permissions
        if not (perms.administrator or perms.manage_guild or perms.manage_roles):
            await interaction.response.send_message(
                'このコマンドは管理者のみ使用できます。',
                ephemeral=True,
            )
            return

        guild_id = interaction.guild.id
        lobby, _ = self.store.snapshot(guild_id)

        if not lobby:
            await interaction.response.send_message(
                'ロビーには誰もいません。',
                ephemeral=True,
            )
            return

        # leave() を使って全員抜けさせる（永続化も中でやる）
        count = 0
        for uid in list(lobby):
            self.store.leave(guild_id, uid)
            count += 1

        await interaction.response.send_message(
            f'ロビーを解散しました。（{count}人）',
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Lobby(bot))
