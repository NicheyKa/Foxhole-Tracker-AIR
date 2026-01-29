# Полная версия бота Foxhole Tracker
# Включает: start_war, destroy, stats, history, live-лидерборды

import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
from datetime import datetime, timezone
import asyncio

# ================== НАСТРОЙКИ ==================

import os
TOKEN = os.getenv("DISCORD_TOKEN")
UPDATE_INTERVAL = 30

# ================== БОТ ==================

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================== ТЕХНИКА ==================

VEHICLES = {
    "Логистика": 1,
    "Легкобронированная техника": 2,
    "Легкие танки": 3,
    "Средние танки": 5,
    "Тяжелые танки": 8,
    "Разведывательные самолеты": 3,
    "Малая авиация": 5,
    "Крупная авиация": 8,
    "Малый флот": 4,
    "Крупный флот": 10
}

# ================== БАЗА ДАННЫХ ==================

db = sqlite3.connect("foxhole.db")
cursor = db.cursor()

cursor.executescript("""
CREATE TABLE IF NOT EXISTS wars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    active INTEGER,
    started_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER,
    war_id INTEGER,
    points INTEGER,
    PRIMARY KEY (user_id, war_id)
);

CREATE TABLE IF NOT EXISTS stats (
    user_id INTEGER,
    war_id INTEGER,
    vehicle TEXT,
    display_name TEXT,
    count INTEGER,
    PRIMARY KEY (user_id, war_id, display_name)
);

CREATE TABLE IF NOT EXISTS live_messages (
    war_id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    leaderboard_msg INTEGER,
    vehicles_msg INTEGER
);
""")
db.commit()

# ================== УТИЛИТЫ ==================

def get_active_war():
    cursor.execute("SELECT id, name FROM wars WHERE active = 1")
    return cursor.fetchone()

def get_war_by_number(number: str):
    cursor.execute("SELECT id, name FROM wars WHERE name = ?", (number,))
    return cursor.fetchone()

# ================== READY ==================

@bot.event
async def on_ready():
    await bot.tree.sync()
    bot.loop.create_task(live_update_loop())
    print(f"Бот запущен как {bot.user}")

# ================== START WAR ==================

@bot.tree.command(name="start_war", description="Начать новую войну")
async def start_war(interaction: discord.Interaction, number: str):
    # ─── проверка: существует ли война с таким номером ───
    cursor.execute("SELECT id FROM wars WHERE name = ?", (number,))
    exists = cursor.fetchone()

    if exists:
        await interaction.response.send_message(
            f"❌ Война с номером **#{number}** уже существует",
            ephemeral=True
        )
        return

    # ─── деактивируем предыдущие войны ───
    cursor.execute("UPDATE wars SET active = 0")

    # ─── создаём новую ───
    cursor.execute(
        "INSERT INTO wars (name, active, started_at) VALUES (?, 1, ?)",
        (number, datetime.now(timezone.utc).isoformat())
    )
    db.commit()

    war_id, _ = get_active_war()
    channel = interaction.channel

    lb = await channel.send("⏳ Создаю лидерборд...")
    veh = await channel.send("⏳ Создаю таблицу техники...")

    cursor.execute(
        "INSERT OR REPLACE INTO live_messages VALUES (?, ?, ?, ?)",
        (war_id, channel.id, lb.id, veh.id)
    )
    db.commit()

    await interaction.response.send_message(
        f"⚔️ **Начата война Foxhole #{number}**",
        ephemeral=True
    )

# ================== DESTROY ==================

# ─── Проверка офицерской роли ───

def is_officer(member: discord.Member) -> bool:
    """
    Проверяет, является ли пользователь офицером.
    По умолчанию: роль с названием 'Officer' или 'Офицер'.
    """
    return any(role.name.lower() in ("officer", "офицер") for role in member.roles)



@bot.tree.command(name="destroy", description="Отметить уничтоженную технику")
@app_commands.describe(
    vehicle="Категория техники",
    amount="Количество",
    custom_name="Кастомное название техники (опционально)"
)
@app_commands.choices(vehicle=[app_commands.Choice(name=v, value=v) for v in VEHICLES])
async def destroy(
    interaction: discord.Interaction,
    vehicle: app_commands.Choice[str],
    amount: int = 1,
    custom_name: str | None = None
):
    war = get_active_war()
    if not war:
        await interaction.response.send_message("❌ Нет активной войны", ephemeral=True)
        return

    war_id, _ = war
    uid = interaction.user.id

    display_name = custom_name.strip() if custom_name else vehicle.value
    pts = VEHICLES[vehicle.value] * amount

    cursor.execute("INSERT OR IGNORE INTO users VALUES (?, ?, 0)", (uid, war_id))
    cursor.execute(
        "UPDATE users SET points = points + ? WHERE user_id = ? AND war_id = ?",
        (pts, uid, war_id)
    )

    cursor.execute(
        """
        INSERT INTO stats (user_id, war_id, vehicle, display_name, count)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, war_id, display_name)
        DO UPDATE SET count = count + ?
        """,
        (uid, war_id, vehicle.value, display_name, amount, amount)
    )

    db.commit()

    await interaction.response.send_message(
        f"✅ {interaction.user.display_name}: **{amount} × {display_name}** (+{pts})"
    )

# ================== ОФИЦЕРСКОЕ РЕДАКТИРОВАНИЕ ==================

@bot.tree.command(
    name="edit_destroy",
    description="[Офицеры] Исправить запись уничтоженной техники"
)
@app_commands.describe(
    user="Игрок, чью статистику нужно исправить",
    vehicle="Категория техники",
    delta="На сколько изменить значение (может быть отрицательным)",
    custom_name="Кастомное название техники (опционально)"
)
@app_commands.choices(vehicle=[app_commands.Choice(name=v, value=v) for v in VEHICLES])
async def edit_destroy(
    interaction: discord.Interaction,
    user: discord.Member,
    vehicle: app_commands.Choice[str],
    delta: int,
    custom_name: str | None = None
):
    # ─── проверка прав ───
    if not is_officer(interaction.user):
        await interaction.response.send_message(
            "❌ У тебя нет прав офицера",
            ephemeral=True
        )
        return

    war = get_active_war()
    if not war:
        await interaction.response.send_message(
            "❌ Нет активной войны",
            ephemeral=True
        )
        return

    war_id, _ = war

    display_name = custom_name.strip() if custom_name else vehicle.value

    # ─── текущее значение ───
    cursor.execute(
        """
        SELECT count
        FROM stats
        WHERE user_id = ?
          AND war_id = ?
          AND vehicle = ?
          AND display_name = ?
        """,
        (user.id, war_id, vehicle.value, display_name)
    )
    row = cursor.fetchone()
    current = row[0] if row else 0

    new_value = max(0, current + delta)

    # ─── обновляем / создаём запись ───
    if row:
        cursor.execute(
            """
            UPDATE stats
            SET count = ?
            WHERE user_id = ?
              AND war_id = ?
              AND vehicle = ?
              AND display_name = ?
            """,
            (new_value, user.id, war_id, vehicle.value, display_name)
        )
    else:
        cursor.execute(
            """
            INSERT INTO stats (user_id, war_id, vehicle, display_name, count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user.id, war_id, vehicle.value, display_name, new_value)
        )

    # ─── пересчёт очков ───
    points_delta = VEHICLES[vehicle.value] * delta

    cursor.execute(
        "INSERT OR IGNORE INTO users VALUES (?, ?, 0)",
        (user.id, war_id)
    )
    cursor.execute(
        """
        UPDATE users
        SET points = points + ?
        WHERE user_id = ? AND war_id = ?
        """,
        (points_delta, user.id, war_id)
    )

    db.commit()

    await interaction.response.send_message(
        f"🛠 **Исправление внесено**\n"
        f"Игрок: **{user.display_name}**\n"
        f"Категория: **{vehicle.value}**\n"
        f"Техника: **{display_name}**\n"
        f"Было: {current} → Стало: {new_value}\n"
        f"Очки изменены на: {points_delta}"
    )

# ================== ОБЩАЯ ЛОГИКА СТАТИСТИКИ ==================

async def show_stats(interaction, war_number, target, vehicle=None):
    # ─── война ───
    if war_number:
        data = get_war_by_number(war_number)
        if not data:
            await interaction.response.send_message("❌ Война не найдена")
            return
        war_id, war_name = data
    else:
        data = get_active_war()
        if not data:
            await interaction.response.send_message("❌ Нет активной войны")
            return
        war_id, war_name = data

    # ─── ОДНА КАТЕГОРИЯ ───
    if vehicle:
        cursor.execute(
            """
            SELECT display_name, SUM(count)
            FROM stats
            WHERE user_id = ? AND war_id = ? AND vehicle = ?
            GROUP BY display_name
            ORDER BY SUM(count) DESC
            """,
            (target.id, war_id, vehicle)
        )
        rows = cursor.fetchall()

        if not rows:
            await interaction.response.send_message("📭 Нет данных")
            return

        text = "\n".join(f"• {name} — {count}" for name, count in rows)

        await interaction.response.send_message(
            f"📊 **{target.display_name}**\n"
            f"⚔️ Война #{war_name}\n"
            f"🚗 **{vehicle}**\n{text}"
        )
        return  # ⬅️ ВАЖНО

    # ─── ВСЯ СТАТИСТИКА ───
    cursor.execute(
        """
        SELECT vehicle, display_name, SUM(count) as total
        FROM stats
        WHERE user_id = ? AND war_id = ?
        GROUP BY vehicle, display_name
        ORDER BY vehicle, total DESC
        """,
        (target.id, war_id)
    )
    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message("📭 Нет данных")
        return

    cursor.execute(
        "SELECT points FROM users WHERE user_id = ? AND war_id = ?",
        (target.id, war_id)
    )
    points = cursor.fetchone()[0]

    embed = discord.Embed(
        title=f"📊 {target.display_name}",
        description=f"⚔️ Война #{war_name}",
        color=discord.Color.orange()
    )

    from collections import defaultdict
    grouped = defaultdict(list)

    for vehicle, name, count in rows:
        grouped[vehicle].append((name, count))

    for vehicle, items in grouped.items():
        text = "\n".join(f"• {name} — {count}" for name, count in items)
        embed.add_field(
            name=f"🚗 {vehicle}",
            value=text,
            inline=False
        )

    embed.set_footer(text=f"Очки: {points}")
    await interaction.response.send_message(embed=embed)

# ================== STATS ==================

@bot.tree.command(name="stats", description="Статистика игрока")
@app_commands.describe(war="Номер войны", user="Игрок", vehicle="Тип техники")
@app_commands.choices(vehicle=[app_commands.Choice(name=v, value=v) for v in VEHICLES])
async def stats(interaction, war: str | None = None, user: discord.Member | None = None, vehicle: app_commands.Choice[str] | None = None):
    await show_stats(
        interaction,
        war,
        user or interaction.user,
        vehicle.value if vehicle else None
    )

# ================== HISTORY ==================

history = app_commands.Group(name="history", description="История войн")

@history.command(name="list")
async def history_list(interaction):
    cursor.execute("SELECT name, started_at FROM wars WHERE active = 0 ORDER BY started_at DESC LIMIT 10")
    rows = cursor.fetchall()
    if not rows:
        await interaction.response.send_message("📭 Нет прошлых войн")
        return

    embed = discord.Embed(title="📜 Прошлые войны", color=discord.Color.blurple())
    for n, d in rows:
        embed.add_field(name=f"Война #{n}", value=d.split("T")[0], inline=False)

    await interaction.response.send_message(embed=embed)

@history.command(name="war")
async def history_war(interaction, war: str):
    await show_stats(interaction, war, interaction.user)

@history.command(name="top")
async def history_top(interaction, war: str):
    data = get_war_by_number(war)
    if not data:
        await interaction.response.send_message("❌ Война не найдена")
        return

    war_id, war_name = data
    cursor.execute(
        "SELECT user_id, points FROM users WHERE war_id = ? ORDER BY points DESC LIMIT 10",
        (war_id,)
    )
    rows = cursor.fetchall()

    embed = discord.Embed(title=f"🏆 Лидерборд — Война #{war_name}", color=discord.Color.gold())
    for i, (uid, pts) in enumerate(rows, 1):
        user = await bot.fetch_user(uid)
        embed.add_field(name=f"{i}. {user.name}", value=str(pts), inline=False)

    await interaction.response.send_message(embed=embed)

bot.tree.add_command(history)

# ================== LIVE UPDATE ==================

async def live_update_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await update_live()
        await asyncio.sleep(UPDATE_INTERVAL)

async def update_live():
    war = get_active_war()
    if not war:
        return

    war_id, war_name = war
    cursor.execute("SELECT channel_id, leaderboard_msg, vehicles_msg FROM live_messages WHERE war_id = ?", (war_id,))
    row = cursor.fetchone()
    if not row:
        return

    channel_id, lb_id, veh_id = row
    channel = bot.get_channel(channel_id)
    if not channel:
        return

    cursor.execute("SELECT user_id, points FROM users WHERE war_id = ? ORDER BY points DESC LIMIT 10", (war_id,))
    rows = cursor.fetchall()

    lines = []
    for i, (uid, pts) in enumerate(rows, 1):
        try:
            user = await bot.fetch_user(uid)
            lines.append(f"**{i}. {user.name}** — {pts}")
        except Exception:
            continue

    lb_text = "\n".join(lines) if lines else "Нет данных"

    cursor.execute(
        "SELECT vehicle, SUM(count) FROM stats WHERE war_id = ? GROUP BY vehicle ORDER BY SUM(count) DESC",
        (war_id,)
    )
    rows = cursor.fetchall()
    veh_text = "\n".join(f"**{v}** — {c}" for v, c in rows) if rows else "Нет данных"

    timestamp = datetime.now(timezone.utc).strftime('%H:%M UTC')

    await (await channel.fetch_message(lb_id)).edit(
        content=f"🏆 **Лидерборд — Война #{war_name}**\n\n{lb_text}\n\n⏱ {timestamp}"
    )
    await (await channel.fetch_message(veh_id)).edit(
        content=f"🚗 **Техника — Война #{war_name}**\n\n{veh_text}\n\n⏱ {timestamp}"
    )

# ================== RUN ==================

bot.run(TOKEN)