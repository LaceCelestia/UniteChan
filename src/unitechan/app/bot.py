import os
import logging

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix='!', intents=intents)

    @bot.event
    async def on_ready() -> None:
        logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')

    @bot.tree.command(name='sync', description='このサーバーのスラッシュコマンドを再同期します(管理者専用)')
    @app_commands.checks.has_permissions(administrator=True)
    async def sync_commands(interaction: discord.Interaction) -> None:
        synced = await bot.tree.sync(guild=interaction.guild)
        await interaction.response.send_message(
            f'このサーバーのコマンドを再同期しました。\n{len(synced)} 個のコマンドを更新しました。',
            ephemeral=True,
        )

    async def load_cogs() -> None:
        # 既存コマンド群
        try:
            await bot.load_extension('unitechan.app.cogs.team_split')
        except Exception as exc:
            logger.exception('failed to load team_split: %s', exc)
        try:
            await bot.load_extension('unitechan.app.cogs.lobby')
        except Exception as exc:
            logger.exception('failed to load lobby: %s', exc)
        # /config 系
        try:
            await bot.load_extension('unitechan.app.cogs.config_commands')
        except Exception as exc:
            logger.exception('failed to load config_commands: %s', exc)
        # /ban 系
        try:
            await bot.load_extension('unitechan.app.cogs.ban_commands')
        except Exception as exc:
            logger.exception('failed to load ban_commands: %s', exc)
        # /result /stats 系
        try:
            await bot.load_extension('unitechan.app.cogs.result_commands')
        except Exception as exc:
            logger.exception('failed to load result_commands: %s', exc)

    async def setup_hook() -> None:  # type: ignore[override]
        await load_cogs()
        # グローバルツリー同期
        await bot.tree.sync()

    bot.setup_hook = setup_hook  # type: ignore[assignment]
    return bot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [%(levelname)s] %(name)s: %(message)s',
    )

    token = os.getenv('DISCORD_TOKEN')
    if not token:
        raise RuntimeError('環境変数 DISCORD_TOKEN がありません (.env に書く)')

    bot = create_bot()
    bot.run(token)


if __name__ == '__main__':
    main()
