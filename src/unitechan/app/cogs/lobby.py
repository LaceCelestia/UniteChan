import discord
from discord import app_commands
from discord.ext import commands

from unitechan.core.lobby_store import LobbyStore


class Lobby(commands.Cog):
    '''ユナイト用のロビー管理 Cog'''

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = LobbyStore()

    # ---- ヘルパー ----

    async def _require_admin(self, interaction: discord.Interaction) -> bool:
        """管理者権限チェック。権限不足なら ephemeral エラーを送り False を返す。"""
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return False
        perms = interaction.user.guild_permissions
        if not (perms.administrator or perms.manage_guild or perms.manage_roles):
            await interaction.response.send_message(
                'このコマンドは管理者のみ使用できます。', ephemeral=True,
            )
            return False
        return True

    # ---- コマンド ----

    @app_commands.command(name='join', description='ユナイト用ロビーに参加します')
    @app_commands.describe(member='参加させるメンバー（省略時は自分）管理者専用')
    async def join(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return

        if member is not None and not await self._require_admin(interaction):
            return

        target = member or interaction.user
        guild_id = interaction.guild.id
        lobby = self.store.get_lobby(guild_id)

        if target.id in lobby:
            msg = (
                f'{target.display_name} はすでにロビーに参加しています。\n現在人数: **{len(lobby)}人**'
                if member else
                f'すでにロビーに参加しています。\n現在人数: **{len(lobby)}人**'
            )
            await interaction.response.send_message(msg, ephemeral=True)
            return

        size = self.store.join(guild_id, target.id)
        msg = (
            f'{target.display_name} をロビーに追加しました。\n現在人数: **{size}人**'
            if member else
            f'ロビーに参加しました！\n現在人数: **{size}人**'
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name='leave', description='ロビーから抜けます')
    async def leave(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return

        guild_id = interaction.guild.id
        lobby = self.store.get_lobby(guild_id)

        if interaction.user.id not in lobby:
            await interaction.response.send_message('ロビーに参加していません。', ephemeral=True)
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
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return

        self.store.set_rank(interaction.guild.id, interaction.user.id, rank.value)
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
        if not await self._require_admin(interaction):
            return

        ok = self.store.kick(interaction.guild.id, member.id)  # type: ignore[union-attr]
        if not ok:
            await interaction.response.send_message('そのメンバーはロビーにいません。', ephemeral=True)
            return

        await interaction.response.send_message(
            f'{member.display_name} をロビーから削除しました。',
            ephemeral=True,
        )

    @app_commands.command(name='lobby', description='現在のロビーメンバーを表示します')
    async def lobby(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return

        lobby, ranks = self.store.snapshot(interaction.guild.id)

        if not lobby:
            await interaction.response.send_message('ロビーには誰もいません。', ephemeral=True)
            return

        lines = '\n'.join(
            f'- <@{uid}> ({ranks[uid]})' if uid in ranks else f'- <@{uid}>'
            for uid in lobby
        )
        await interaction.response.send_message(
            f'**現在のロビー（{len(lobby)}人）**\n{lines}',
            ephemeral=True,
        )

    @app_commands.command(name='lobby_collect', description='今いるVCのメンバーをロビーに一括登録します（管理者専用）')
    async def lobby_collect(self, interaction: discord.Interaction) -> None:
        if not await self._require_admin(interaction):
            return

        user = interaction.user  # type: ignore[assignment]  # _require_admin 済み
        vc = user.voice and user.voice.channel
        if vc is None:
            await interaction.response.send_message('あなたがVCに参加していません。', ephemeral=True)
            return

        members = [m for m in vc.members if not m.bot]
        if not members:
            await interaction.response.send_message(
                f'**{vc.name}** にBotでないメンバーがいません。', ephemeral=True,
            )
            return

        self.store.set_members(interaction.guild.id, {m.id for m in members})  # type: ignore[union-attr]

        lines = '\n'.join(f'- {m.display_name}' for m in members)
        await interaction.response.send_message(
            f'**{vc.name}** のメンバー {len(members)}人 をロビーに登録しました。\n{lines}',
            ephemeral=True,
        )

    @app_commands.command(name='lobby_clear', description='ロビーを全員解散します（管理者専用）')
    async def lobby_clear(self, interaction: discord.Interaction) -> None:
        if not await self._require_admin(interaction):
            return

        guild_id = interaction.guild.id  # type: ignore[union-attr]
        lobby, _ = self.store.snapshot(guild_id)

        if not lobby:
            await interaction.response.send_message('ロビーには誰もいません。', ephemeral=True)
            return

        count = len(lobby)
        self.store.set_members(guild_id, set())
        await interaction.response.send_message(f'ロビーを解散しました。（{count}人）', ephemeral=True)


    @app_commands.command(name='name', description='表示名（ニックネーム）を変更します')
    @app_commands.describe(
        name='新しい表示名（省略するとリセット）',
        member='変更するメンバー（省略時は自分） ★管理者専用',
    )
    async def set_name(
        self,
        interaction: discord.Interaction,
        name: str | None = None,
        member: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return

        if member is not None and not await self._require_admin(interaction):
            return

        target = member or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return

        self.store.set_alias(interaction.guild.id, target.id, name)  # type: ignore[union-attr]

        if name:
            msg = f'**{target.display_name}** の表示名を **{name}** に変更しました。' if member else f'表示名を **{name}** に変更しました。'
        else:
            msg = f'**{target.display_name}** の表示名をリセットしました。' if member else '表示名をリセットしました。'
        await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Lobby(bot))
