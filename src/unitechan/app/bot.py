import os
import logging

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)


_COGS = [
    'unitechan.app.cogs.team_split',
    'unitechan.app.cogs.lobby',
    'unitechan.app.cogs.config_commands',
    'unitechan.app.cogs.ban_commands',
    'unitechan.app.cogs.result_commands',
    'unitechan.app.cogs.separate_commands',
]


class UniteChanBot(commands.Bot):
    async def setup_hook(self) -> None:
        for cog in _COGS:
            try:
                await self.load_extension(cog)
            except Exception as exc:
                logger.exception('failed to load %s: %s', cog, exc)
        await self.tree.sync()

    async def on_ready(self) -> None:
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')  # type: ignore[union-attr]


def create_bot() -> UniteChanBot:
    intents = discord.Intents.default()
    intents.message_content = True

    bot = UniteChanBot(command_prefix='!', intents=intents)

    @bot.tree.command(name='sync', description='このサーバーのスラッシュコマンドを再同期します(管理者専用)')
    @app_commands.checks.has_permissions(administrator=True)
    async def sync_commands(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        synced = await bot.tree.sync(guild=interaction.guild)
        await interaction.followup.send(
            f'このサーバーのコマンドを再同期しました。\n{len(synced)} 個のコマンドを更新しました。',
            ephemeral=True,
        )

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
