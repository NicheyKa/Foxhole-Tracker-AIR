import discord

def edit_result_embed(user, vehicle, name, before, after, points_delta):
    embed = discord.Embed(
        title="🛠 Исправление внесено",
        color=discord.Color.orange()
    )
    embed.add_field(name="Игрок", value=user.display_name, inline=False)
    embed.add_field(name="Категория", value=vehicle, inline=True)
    embed.add_field(name="Техника", value=name, inline=True)
    embed.add_field(name="Было → Стало", value=f"{before} → {after}", inline=False)
    embed.set_footer(text=f"Очки: {points_delta:+}")
    return embed