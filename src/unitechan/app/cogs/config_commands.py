import discord
from discord import app_commands
from discord.ext import commands

from unitechan.core.config_store import get_store


config_group = app_commands.Group(name='config', description='ユナイトちゃんの設定')


@config_group.command(name='role_balance', description='b=2 用のロール構成(1チーム分)を設定します')
@app_commands.describe(
    atk='アタック型(Attacker) の人数',
    all='バランス型(All-rounder) の人数',
    spd='スピード型(Speedster) の人数',
    deff='ディフェンス型(Defender) の人数',
    sup='サポート型(Supporter) の人数',
)
async def config_role_balance(
    interaction: discord.Interaction,
    atk: app_commands.Range[int, 0, 5],
    all: app_commands.Range[int, 0, 5],
    spd: app_commands.Range[int, 0, 5],
    deff: app_commands.Range[int, 0, 5],
    sup: app_commands.Range[int, 0, 5],
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
        return

    total = atk + all + spd + deff + sup
    if total != 5:
        await interaction.response.send_message(
            f'1チーム5人想定なので、合計が5になるように設定してください。(現在: {total})',
            ephemeral=True,
        )
        return

    store = get_store()
    store.set_role_balance_targets(
        guild_id=interaction.guild.id,
        attacker=atk,
        all_rounder=all,
        speedster=spd,
        defender=deff,
        supporter=sup,
    )

    msg = f'ロールバランスを設定しました。\n1チーム想定: ATK={atk} ALL={all} SPD={spd} DEF={deff} SUP={sup}'
    await interaction.response.send_message(msg, ephemeral=True)


@config_group.command(name='avoid', description='連続ロール回避の回数を設定します (0〜5)')
@app_commands.describe(count='直近何回分のロールと被らないようにするか (0で無効)')
async def config_avoid(
    interaction: discord.Interaction,
    count: app_commands.Range[int, 0, 5],
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
        return

    store = get_store()
    store.set_avoid_count(interaction.guild.id, count)

    await interaction.response.send_message(
        f'連続ロール回避を avoid={count} に設定しました。(0で無効, 最大5)',
        ephemeral=True,
    )


@config_group.command(name='reset', description='このサーバーの /split 関連設定をリセットします')
async def config_reset(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
        return

    store = get_store()
    store.reset_guild(interaction.guild.id)
    await interaction.response.send_message('このサーバーの /split 関連設定をリセットしました。', ephemeral=True)


@config_group.command(name='show', description='このサーバーの /split 設定を表示します')
async def config_show(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message('サーバー内で使ってね。', ephemeral=True)
        return

    store = get_store()
    text = store.describe_split_config(interaction.guild.id)
    await interaction.response.send_message(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(config_group)
