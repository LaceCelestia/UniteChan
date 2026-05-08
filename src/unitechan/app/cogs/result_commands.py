from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from unitechan.core.stats_store import get_stats_store
from unitechan.core.lobby_store import LobbyStore
from unitechan.core.split_service import SplitService
from unitechan.app.cogs._utils import is_admin

_JST = timezone(timedelta(hours=9))
_MAX_STATS_RANKING_ROWS = 25
_MAX_IMPORT_BYTES = 1_000_000


async def _resolve_name(guild: discord.Guild, uid: int, lobby_store: LobbyStore) -> str:
    alias = lobby_store.get_alias(guild.id, uid)
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

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.lobby_store = LobbyStore()

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
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
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

        await interaction.response.defer()
        if store.get_last_match(guild_id) != last:
            await interaction.followup.send(
                'この試合はすでに記録済み、または別の試合に更新されています。',
                ephemeral=True,
            )
            return

        winning_idx = 0 if team == 'a' else 1
        committed = False
        split_cog = interaction.client.get_cog('TeamSplit')
        if split_cog is not None and hasattr(split_cog, 'commit_history_for_teams'):
            committed = split_cog.commit_history_for_teams(guild_id, last)
        if not committed:
            service = SplitService(LobbyStore())
            if not service.commit_teams_with_stored_context(guild_id, last):
                service.commit_teams(guild_id, last)
        winners, losers = store.record_result(guild_id, winning_idx)
        if not winners:
            await interaction.followup.send(
                '記録できる試合が見つかりませんでした。最新のチーム分けを確認してください。',
                ephemeral=True,
            )
            return

        resolved_names = await asyncio.gather(
            *(
                _resolve_name(interaction.guild, uid, self.lobby_store)
                for uid in [*winners, *losers]
            )
        )
        winner_names = list(resolved_names[:len(winners)])
        loser_names = list(resolved_names[len(winners):])

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
        await interaction.followup.send(embed=embed)

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

        await interaction.response.defer()

        display_records = sorted_records[:_MAX_STATS_RANKING_ROWS]
        names = await asyncio.gather(
            *(_resolve_name(interaction.guild, uid, self.lobby_store) for uid, _ in display_records)
        )
        lines: List[str] = []
        medals = ['🥇', '🥈', '🥉']
        for rank, ((uid, r), name) in enumerate(zip(display_records, names)):
            games = r['wins'] + r['losses']
            rate = f"{r['wins'] / games * 100:.1f}%" if games else '-%'
            prefix = medals[rank] if rank < 3 else f'**{rank + 1}.**'
            lines.append(f"{prefix} {name}　{r['wins']}勝 {r['losses']}敗　勝率 {rate}")
        hidden = len(sorted_records) - len(display_records)
        if hidden > 0:
            lines.append(f'ほか {hidden}人')

        embed = discord.Embed(title=title, description='\n'.join(lines))
        await interaction.followup.send(embed=embed)

    @stats.command(name='reset', description='このサーバーの戦績をすべてリセットします（管理者専用）')
    async def stats_reset(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
            return

        count = get_stats_store().reset_stats(interaction.guild.id)
        SplitService.clear_history_cache(interaction.guild.id)
        if count == 0:
            await interaction.response.send_message('リセットする戦績がありません。', ephemeral=True)
            return
        await interaction.response.send_message(f'✅ {count}人分の戦績をリセットしました。', ephemeral=True)

    @stats.command(name='export', description='戦績データをJSONファイルでエクスポートします')
    async def stats_export(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
            return

        data = get_stats_store().export_stats(interaction.guild.id)
        if not data['records']:
            await interaction.response.send_message('エクスポートする戦績がありません。', ephemeral=True)
            return

        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        date_str = datetime.now(_JST).strftime('%Y%m%d')
        filename = f'stats_{interaction.guild.id}_{date_str}.json'
        await interaction.response.send_message(
            '📤 戦績データをエクスポートしました。',
            file=discord.File(io.BytesIO(json_bytes), filename=filename),
        )

    @stats.command(name='import', description='戦績データをJSONファイルからインポート・マージします（管理者専用）')
    @app_commands.describe(file='エクスポートしたJSONファイル')
    async def stats_import(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
            return
        if not is_admin(interaction):
            await interaction.response.send_message('このコマンドは管理者のみ使用できます。', ephemeral=True)
            return
        if not file.filename.endswith('.json'):
            await interaction.response.send_message('JSONファイルを添付してください。', ephemeral=True)
            return
        if file.size is not None and file.size > _MAX_IMPORT_BYTES:
            await interaction.response.send_message('JSONファイルが大きすぎます。1MB以下にしてください。', ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            raw = await file.read()
            if len(raw) > _MAX_IMPORT_BYTES:
                await interaction.followup.send('JSONファイルが大きすぎます。1MB以下にしてください。', ephemeral=True)
                return
            data = json.loads(raw.decode('utf-8'))
            result = get_stats_store().merge_stats(interaction.guild.id, data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            await interaction.followup.send('ファイルの読み込みに失敗しました。正しいJSONファイルか確認してください。', ephemeral=True)
            return
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        players = len(data.get('records', {}))
        await interaction.followup.send(
            f'✅ {players}人分の戦績をマージしました。'
            f'（+{result["added_wins"]}勝 / +{result["added_losses"]}敗）',
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ResultCommands(bot))
