from __future__ import annotations

from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from unitechan.core.stats_store import get_stats_store
from unitechan.core.lobby_store import LobbyStore
from unitechan.app.cogs._utils import is_admin


async def _resolve_name(guild: discord.Guild, uid: int) -> str:
    alias = LobbyStore().get_alias(guild.id, uid)
    if alias:
        return alias
    m = guild.get_member(uid)
    if m:
        return m.display_name
    try:
        m = await guild.fetch_member(uid)
        return m.display_name
    except Exception:
        return f'<@{uid}>'


class ResultCommands(commands.Cog):
    """試合結果・戦績管理 Cog"""

    # ---- /result ----

    @app_commands.command(name='result_undo', description='直前の試合結果記録を取り消します（管理者専用）')
    async def result_undo(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
            return

        ok = get_stats_store().undo_last_result(interaction.guild.id)
        if not ok:
            await interaction.response.send_message('取り消せる記録がありません。', ephemeral=True)
            return

        await interaction.response.send_message('↩️ 直前の試合結果を取り消しました。', ephemeral=True)

    @app_commands.command(name='result', description='試合結果を記録します（Team A または Team B の勝利）')
    @app_commands.describe(team='勝利したチーム')
    @app_commands.choices(team=[
        app_commands.Choice(name='Team A', value='a'),
        app_commands.Choice(name='Team B', value='b'),
    ])
    async def result(self, interaction: discord.Interaction, team: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return

        store = get_stats_store()
        guild_id = interaction.guild.id

        last = store.get_last_match(guild_id)
        if not last:
            await interaction.response.send_message(
                '記録できる試合がありません。先に `/split run` でチーム分けしてください。',
                ephemeral=True,
            )
            return

        winning_idx = 0 if team == 'a' else 1
        winners, losers = store.record_result(guild_id, winning_idx)

        winner_names = [await _resolve_name(interaction.guild, uid) for uid in winners]
        loser_names = [await _resolve_name(interaction.guild, uid) for uid in losers]

        team_label = 'Team A' if team == 'a' else 'Team B'
        embed = discord.Embed(title=f'🏆 {team_label} の勝利！', color=0xf1c40f)
        embed.add_field(
            name='🏆 勝利',
            value='\n'.join(f'・{n}' for n in winner_names) or '(なし)',
            inline=True,
        )
        embed.add_field(
            name='💀 敗北',
            value='\n'.join(f'・{n}' for n in loser_names) or '(なし)',
            inline=True,
        )
        await interaction.response.send_message(embed=embed)

    # ---- /stats ----

    stats = app_commands.Group(name='stats', description='戦績を確認します')

    @stats.command(name='show', description='戦績ランキングまたは個人の戦績を表示します')
    @app_commands.describe(
        member='指定すると個人の戦績を表示（省略時はランキング）',
        period='表示する期間（省略時は通算）',
    )
    @app_commands.choices(period=[
        app_commands.Choice(name='通算', value='all'),
        app_commands.Choice(name='当日（05:00リセット）', value='today'),
    ])
    async def stats_show(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
        period: str = 'all',
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return

        store = get_stats_store()
        guild_id = interaction.guild.id
        is_today = (period == 'today')

        if member is not None:
            if is_today:
                all_daily = store.get_daily_records(guild_id)
                r = all_daily.get(member.id, {'wins': 0, 'losses': 0})
            else:
                r = store.get_record(guild_id, member.id)
            games = r['wins'] + r['losses']
            rate = f"{r['wins'] / games * 100:.1f}%" if games else '-%'
            period_label = '当日' if is_today else '通算'
            embed = discord.Embed(
                title=f'📊 {member.display_name} の戦績（{period_label}）',
                description=f'**{r["wins"]}勝 {r["losses"]}敗**　勝率 {rate}　({games}試合)',
            )
            await interaction.response.send_message(embed=embed)
            return

        # ランキング表示
        if is_today:
            all_records = store.get_daily_records(guild_id)
            title = '🏆 当日戦績ランキング'
        else:
            all_records = store.get_all_records(guild_id)
            title = '🏆 通算戦績ランキング'

        if not all_records:
            await interaction.response.send_message('まだ戦績がありません。', ephemeral=True)
            return

        # 勝利数降順 → 勝率降順でソート
        def sort_key(item: tuple) -> tuple:
            r = item[1]
            games = r['wins'] + r['losses']
            rate = r['wins'] / games if games else 0.0
            return (-r['wins'], -rate)

        sorted_records = sorted(all_records.items(), key=sort_key)

        lines: List[str] = []
        medals = ['🥇', '🥈', '🥉']
        for rank, (uid, r) in enumerate(sorted_records):
            name = await _resolve_name(interaction.guild, uid)
            games = r['wins'] + r['losses']
            rate = f"{r['wins'] / games * 100:.1f}%" if games else '-%'
            prefix = medals[rank] if rank < 3 else f'**{rank + 1}.**'
            lines.append(f"{prefix} {name}　{r['wins']}勝 {r['losses']}敗　勝率 {rate}")

        embed = discord.Embed(title=title, description='\n'.join(lines))
        await interaction.response.send_message(embed=embed)

    @stats.command(name='reset', description='このサーバーの戦績をすべてリセットします（管理者専用）')
    async def stats_reset(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
            return

        count = get_stats_store().reset_stats(interaction.guild.id)
        if count == 0:
            await interaction.response.send_message('リセットする戦績がありません。', ephemeral=True)
            return
        await interaction.response.send_message(f'✅ {count}人分の戦績をリセットしました。', ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ResultCommands(bot))
