from __future__ import annotations

import discord


def is_admin(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    p = interaction.user.guild_permissions
    return p.administrator or p.manage_guild or p.manage_roles
