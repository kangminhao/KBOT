import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import pytz
import uuid

# 載入環境變數
load_dotenv()

# 設定
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
DEFAULT_REMINDER_MINUTES = int(os.getenv('DEFAULT_REMINDER_MINUTES', 30))

# 台灣時區
TW_TZ = pytz.timezone('Asia/Taipei')

# 資料檔案路徑（Railway Volume 掛載時設定 DATA_DIR 環境變數）
DATA_FILE = os.path.join(os.getenv('DATA_DIR', '.'), 'lessons.json')

# 星期對應
WEEKDAY_MAP = {
    '一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6,
    '週一': 0, '週二': 1, '週三': 2, '週四': 3, '週五': 4, '週六': 5, '週日': 6,
    '星期一': 0, '星期二': 1, '星期三': 2, '星期四': 3, '星期五': 4, '星期六': 5, '星期日': 6,
    'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6,
    'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6,
}

WEEKDAY_NAMES = ['週一', '週二', '週三', '週四', '週五', '週六', '週日']


class LessonBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)

        self.scheduler = AsyncIOScheduler(timezone=TW_TZ)
        self.lessons = self.load_lessons()
        self.reminder_minutes = DEFAULT_REMINDER_MINUTES
        self.early_reminder_enabled = True
        self.start_reminder_enabled = True

    def load_lessons(self) -> dict:
        """載入課程資料"""
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 確保有必要欄位
                if 'targets' not in data:
                    data['targets'] = []
                if 'channel_id' not in data:
                    data['channel_id'] = None
                return data
        return {
            'recurring': [],  # 週期性課程
            'one_time': [],   # 單次課程
            'modifications': [],  # 改課記錄
            'targets': [],  # 提醒對象列表 [{'id': user_id, 'name': display_name}]
            'channel_id': None,  # 提醒頻道 ID
            'reminder_minutes': DEFAULT_REMINDER_MINUTES,
            'early_reminder_enabled': True,
            'start_reminder_enabled': True,
        }

    def save_lessons(self):
        """儲存課程資料"""
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.lessons, f, ensure_ascii=False, indent=2)

    async def setup_hook(self):
        """設定 hook，在 bot 啟動時執行"""
        await self.tree.sync()
        print("Slash commands synced!")

    async def on_ready(self):
        print(f'{self.user} 已上線!')
        channel_id = self.lessons.get('channel_id')
        if channel_id:
            print(f'提醒頻道 ID: {channel_id}')
        else:
            print('提醒頻道：尚未設定（請使用 /set_channel 設定）')
        targets = self.lessons.get('targets', [])
        print(f'提醒對象數量: {len(targets)}')

        # 載入提醒設定
        self.reminder_minutes = self.lessons.get('reminder_minutes', DEFAULT_REMINDER_MINUTES)
        self.early_reminder_enabled = self.lessons.get('early_reminder_enabled', True)
        self.start_reminder_enabled = self.lessons.get('start_reminder_enabled', True)

        # 清理過期課程
        self.cleanup_past_lessons()

        # 啟動排程器
        if not self.scheduler.running:
            self.scheduler.start()

        # 設定所有課程的提醒
        await self.schedule_all_reminders()

        print('所有提醒已設定完成!')

    def cleanup_past_lessons(self):
        """清理已過期的一次性課程與改課記錄"""
        now = datetime.now(TW_TZ)
        before_one_time = len(self.lessons.get('one_time', []))
        before_mods = len(self.lessons.get('modifications', []))

        self.lessons['one_time'] = [
            l for l in self.lessons.get('one_time', [])
            if TW_TZ.localize(datetime.fromisoformat(l['datetime'])) + timedelta(minutes=3) > now
        ]
        self.lessons['modifications'] = [
            m for m in self.lessons.get('modifications', [])
            if TW_TZ.localize(datetime.fromisoformat(m['new_datetime'])) + timedelta(minutes=3) > now
        ]

        removed = (before_one_time - len(self.lessons['one_time'])) + (before_mods - len(self.lessons['modifications']))
        if removed > 0:
            self.save_lessons()
            print(f"已清理 {removed} 筆過期課程")

    async def schedule_all_reminders(self):
        """設定所有課程的提醒排程"""
        # 清除現有排程
        self.scheduler.remove_all_jobs()

        # 設定週期性課程提醒
        for lesson in self.lessons.get('recurring', []):
            await self.schedule_recurring_reminder(lesson)

        # 設定單次課程提醒
        for lesson in self.lessons.get('one_time', []):
            await self.schedule_one_time_reminder(lesson)

        # 設定改課提醒
        for mod in self.lessons.get('modifications', []):
            await self.schedule_modification_reminder(mod)

    async def schedule_recurring_reminder(self, lesson: dict):
        """設定週期性課程提醒"""
        weekday = lesson['weekday']
        hour = lesson['hour']
        minute = lesson['minute']
        lesson_id = lesson['id']
        name = lesson.get('name', '韓文課')

        # 計算提醒時間
        reminder_hour = hour
        reminder_minute = minute - self.reminder_minutes

        if reminder_minute < 0:
            reminder_minute += 60
            reminder_hour -= 1
            if reminder_hour < 0:
                reminder_hour = 23
                weekday = (weekday - 1) % 7

        # 使用 cron trigger 設定週期性提醒
        trigger = CronTrigger(
            day_of_week=weekday,
            hour=reminder_hour,
            minute=reminder_minute,
            timezone=TW_TZ
        )

        self.scheduler.add_job(
            self.send_reminder,
            trigger,
            id=f'recurring_{lesson_id}',
            args=[name, hour, minute, lesson_id],
            replace_existing=True
        )

        # 上課時間提醒
        self.scheduler.add_job(
            self.send_start_reminder,
            CronTrigger(day_of_week=lesson['weekday'], hour=lesson['hour'], minute=lesson['minute'], timezone=TW_TZ),
            id=f'start_recurring_{lesson_id}',
            args=[name, lesson_id],
            replace_existing=True
        )

    async def schedule_one_time_reminder(self, lesson: dict):
        """設定單次課程提醒"""
        lesson_time = datetime.fromisoformat(lesson['datetime'])
        lesson_time = TW_TZ.localize(lesson_time) if lesson_time.tzinfo is None else lesson_time
        now = datetime.now(TW_TZ)
        name = lesson.get('name', '韓文課')

        reminder_time = lesson_time - timedelta(minutes=self.reminder_minutes)
        if reminder_time > now:
            self.scheduler.add_job(
                self.send_reminder,
                DateTrigger(run_date=reminder_time, timezone=TW_TZ),
                id=f'onetime_{lesson["id"]}',
                args=[name, lesson_time.hour, lesson_time.minute, lesson['id']],
                replace_existing=True
            )

        if lesson_time > now:
            self.scheduler.add_job(
                self.send_start_reminder,
                DateTrigger(run_date=lesson_time, timezone=TW_TZ),
                id=f'start_onetime_{lesson["id"]}',
                args=[name, lesson['id']],
                replace_existing=True
            )

        # 上課後3分鐘刪除
        delete_time = lesson_time + timedelta(minutes=3)
        if delete_time > now:
            self.scheduler.add_job(
                self.auto_delete_lesson,
                DateTrigger(run_date=delete_time, timezone=TW_TZ),
                id=f'delete_onetime_{lesson["id"]}',
                args=[lesson['id'], 'one_time', name],
                replace_existing=True
            )

    async def schedule_modification_reminder(self, mod: dict):
        """設定改課提醒"""
        new_time = datetime.fromisoformat(mod['new_datetime'])
        new_time = TW_TZ.localize(new_time) if new_time.tzinfo is None else new_time
        now = datetime.now(TW_TZ)
        name = mod.get('name', '韓文課') + '(改課)'

        reminder_time = new_time - timedelta(minutes=self.reminder_minutes)
        if reminder_time > now:
            self.scheduler.add_job(
                self.send_reminder,
                DateTrigger(run_date=reminder_time, timezone=TW_TZ),
                id=f'mod_{mod["id"]}',
                args=[name, new_time.hour, new_time.minute, mod['id']],
                replace_existing=True
            )

        if new_time > now:
            self.scheduler.add_job(
                self.send_start_reminder,
                DateTrigger(run_date=new_time, timezone=TW_TZ),
                id=f'start_mod_{mod["id"]}',
                args=[name, mod['id']],
                replace_existing=True
            )

        # 上課後3分鐘刪除
        delete_time = new_time + timedelta(minutes=3)
        if delete_time > now:
            self.scheduler.add_job(
                self.auto_delete_lesson,
                DateTrigger(run_date=delete_time, timezone=TW_TZ),
                id=f'delete_mod_{mod["id"]}',
                args=[mod['id'], 'modification', name],
                replace_existing=True
            )

    def has_modification_today(self, lesson_id: str) -> bool:
        """檢查週期性課程今天是否有改課記錄"""
        today = datetime.now(TW_TZ).date().isoformat()
        for mod in self.lessons.get('modifications', []):
            if mod.get('original_lesson_id') == lesson_id and mod.get('original_date') == today:
                return True
        return False

    def get_lesson_targets(self, lesson_id: str) -> list:
        """取得課程的提醒對象，如果課程沒有指定則使用全域設定"""
        # 先查找課程特定的對象
        for lesson in self.lessons.get('recurring', []):
            if lesson['id'] == lesson_id and lesson.get('targets'):
                return lesson['targets']
        for lesson in self.lessons.get('one_time', []):
            if lesson['id'] == lesson_id and lesson.get('targets'):
                return lesson['targets']
        for mod in self.lessons.get('modifications', []):
            if mod['id'] == lesson_id and mod.get('targets'):
                return mod['targets']
        # 返回全域設定
        return self.lessons.get('targets', [])

    async def send_start_reminder(self, name: str, lesson_id: str):
        """發送上課時間提醒"""
        if not self.start_reminder_enabled:
            return
        if self.has_modification_today(lesson_id):
            print(f"跳過上課提醒：{name} 本次已改課")
            return
        channel_id = self.lessons.get('channel_id')
        if not channel_id:
            return
        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            targets = self.get_lesson_targets(lesson_id)
            mentions = ' '.join([f"<@{t['id']}>" for t in targets]) if targets else "（未設定提醒對象）"
            await channel.send(f"上課啦!!!!")
        except Exception as e:
            print(f"發送上課提醒失敗：{e}")

    async def auto_delete_lesson(self, lesson_id: str, lesson_type: str, name: str):
        """上課開始後3分鐘自動刪除課程"""
        if lesson_type == 'one_time':
            self.lessons['one_time'] = [l for l in self.lessons.get('one_time', []) if l['id'] != lesson_id]
        elif lesson_type == 'modification':
            self.lessons['modifications'] = [m for m in self.lessons.get('modifications', []) if m['id'] != lesson_id]
        self.save_lessons()
        print(f"已自動刪除課程：{name} ({lesson_id})")

    async def send_reminder(self, name: str, hour: int, minute: int, lesson_id: str):
        """發送提前提醒訊息"""
        if not self.early_reminder_enabled:
            return
        if self.has_modification_today(lesson_id):
            print(f"跳過提前提醒：{name} 本次已改課")
            return
        channel_id = self.lessons.get('channel_id')
        if not channel_id:
            print(f"警告：無法發送提醒，尚未設定提醒頻道")
            return

        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            time_str = f"{hour:02d}:{minute:02d}"
            targets = self.get_lesson_targets(lesson_id)

            if targets:
                mentions = ' '.join([f"<@{t['id']}>" for t in targets])
            else:
                mentions = "（未設定提醒對象）"

            await channel.send(
                f"{mentions}\n提醒：**{name}** 將在 {self.reminder_minutes} 分鐘後開始！\n"
                f"上課時間：{time_str}"
            )

        except Exception as e:
            print(f"發送提醒失敗：{e}")


# 建立 bot 實例
bot = LessonBot()


def parse_weekday(weekday_str: str) -> int:
    """解析星期字串"""
    weekday_str = weekday_str.lower().strip()
    if weekday_str in WEEKDAY_MAP:
        return WEEKDAY_MAP[weekday_str]
    raise ValueError(f"無法解析星期：{weekday_str}")


def parse_time(time_str: str) -> tuple[int, int]:
    """解析時間字串 (支援 20:00, 20點, 8點, 下午3點 等格式)"""
    time_str = time_str.strip()

    # 處理「下午」「晚上」「上午」等前綴
    is_pm = False
    if time_str.startswith(('下午', '晚上')):
        is_pm = True
        time_str = time_str[2:]
    elif time_str.startswith(('上午', '早上')):
        time_str = time_str[2:]

    # 處理 HH:MM 格式
    if ':' in time_str:
        parts = time_str.split(':')
        hour = int(parts[0])
        minute = int(parts[1])
    # 處理 X點Y分 格式
    elif '點' in time_str:
        if '分' in time_str:
            hour = int(time_str.split('點')[0])
            minute = int(time_str.split('點')[1].replace('分', ''))
        else:
            hour = int(time_str.replace('點', ''))
            minute = 0
    else:
        hour = int(time_str)
        minute = 0

    # 處理 12 小時制
    if is_pm and hour < 12:
        hour += 12

    return hour, minute


@bot.tree.command(name="add_lesson", description="新增週期性課程")
@app_commands.describe(
    weekday="星期幾 (例如：三、週三、星期三)",
    time="上課時間 (例如：20:00、晚上8點、下午3點)",
    name="課程名稱 (選填)"
)
async def add_lesson(interaction: discord.Interaction, weekday: str, time: str, name: str = "韓文課"):
    try:
        weekday_num = parse_weekday(weekday)
        hour, minute = parse_time(time)

        lesson = {
            'id': str(uuid.uuid4())[:8],
            'weekday': weekday_num,
            'hour': hour,
            'minute': minute,
            'name': name
        }

        bot.lessons['recurring'].append(lesson)
        bot.save_lessons()
        await bot.schedule_recurring_reminder(lesson)

        time_str = f"{hour:02d}:{minute:02d}"
        await interaction.response.send_message(
            f"已新增週期性課程：**{name}**\n"
            f"時間：每{WEEKDAY_NAMES[weekday_num]} {time_str}\n"
            f"將在上課前 {bot.reminder_minutes} 分鐘提醒"
        )
    except Exception as e:
        await interaction.response.send_message(f"錯誤：{str(e)}")


@bot.tree.command(name="add_single_lesson", description="新增單次課程")
@app_commands.describe(
    date="日期 (格式：YYYY-MM-DD 或 MM-DD)",
    time="上課時間 (例如：20:00、晚上8點)",
    name="課程名稱 (選填)"
)
async def add_single_lesson(interaction: discord.Interaction, date: str, time: str, name: str = "韓文課"):
    try:
        hour, minute = parse_time(time)

        # 解析日期
        if len(date.split('-')) == 2:
            # MM-DD 格式，補上當前年份
            year = datetime.now(TW_TZ).year
            date = f"{year}-{date}"

        lesson_datetime = datetime.strptime(date, "%Y-%m-%d")
        lesson_datetime = lesson_datetime.replace(hour=hour, minute=minute)

        lesson = {
            'id': str(uuid.uuid4())[:8],
            'datetime': lesson_datetime.isoformat(),
            'name': name
        }

        bot.lessons['one_time'].append(lesson)
        bot.save_lessons()
        await bot.schedule_one_time_reminder(lesson)

        await interaction.response.send_message(
            f"已新增單次課程：**{name}**\n"
            f"時間：{lesson_datetime.strftime('%Y-%m-%d %H:%M')}\n"
            f"將在上課前 {bot.reminder_minutes} 分鐘提醒"
        )
    except Exception as e:
        await interaction.response.send_message(f"錯誤：{str(e)}")


@bot.tree.command(name="reschedule", description="改課 - 更改某次課程時間")
@app_commands.describe(
    lesson_id="課程 ID (使用 /list_lessons 查看)",
    new_date="新日期 (格式：YYYY-MM-DD 或 MM-DD)",
    new_time="新時間 (例如：20:00、晚上8點)"
)
async def reschedule(interaction: discord.Interaction, lesson_id: str, new_date: str, new_time: str):
    try:
        hour, minute = parse_time(new_time)

        # 解析日期
        if len(new_date.split('-')) == 2:
            year = datetime.now(TW_TZ).year
            new_date = f"{year}-{new_date}"

        new_datetime = datetime.strptime(new_date, "%Y-%m-%d")
        new_datetime = new_datetime.replace(hour=hour, minute=minute)

        # 查找原課程
        lesson_name = "韓文課"
        original_date = None
        found = False

        for lesson in bot.lessons.get('recurring', []):
            if lesson['id'] == lesson_id:
                lesson_name = lesson.get('name', '韓文課')
                found = True
                # 計算下一次上課的日期作為 original_date
                now = datetime.now(TW_TZ)
                weekday = lesson['weekday']
                lhour = lesson['hour']
                lminute = lesson['minute']
                days_until = (weekday - now.weekday()) % 7
                candidate = now.replace(hour=lhour, minute=lminute, second=0, microsecond=0)
                if days_until == 0 and candidate <= now:
                    days_until = 7
                original_date = (now.date() + timedelta(days=days_until)).isoformat()
                break

        if not found:
            for lesson in bot.lessons.get('one_time', []):
                if lesson['id'] == lesson_id:
                    lesson_name = lesson.get('name', '韓文課')
                    found = True
                    break

        if not found:
            await interaction.response.send_message(f"找不到 ID 為 {lesson_id} 的課程")
            return

        # 新增改課記錄
        mod = {
            'id': str(uuid.uuid4())[:8],
            'original_lesson_id': lesson_id,
            'original_date': original_date,
            'new_datetime': new_datetime.isoformat(),
            'name': lesson_name
        }

        bot.lessons['modifications'].append(mod)
        bot.save_lessons()
        await bot.schedule_modification_reminder(mod)

        await interaction.response.send_message(
            f"已設定改課：**{lesson_name}**\n"
            f"新時間：{new_datetime.strftime('%Y-%m-%d %H:%M')}\n"
            f"將在上課前 {bot.reminder_minutes} 分鐘提醒"
        )
    except Exception as e:
        await interaction.response.send_message(f"錯誤：{str(e)}")


@bot.tree.command(name="list_lessons", description="列出所有課程")
async def list_lessons(interaction: discord.Interaction):
    msg_parts = []

    # 週期性課程
    recurring = bot.lessons.get('recurring', [])
    if recurring:
        msg_parts.append("**📅 週期性課程：**")
        for lesson in recurring:
            time_str = f"{lesson['hour']:02d}:{lesson['minute']:02d}"
            msg_parts.append(
                f"• `{lesson['id']}` - {lesson.get('name', '韓文課')} | "
                f"每{WEEKDAY_NAMES[lesson['weekday']]} {time_str}"
            )

    # 單次課程
    one_time = bot.lessons.get('one_time', [])
    if one_time:
        msg_parts.append("\n**📌 單次課程：**")
        for lesson in one_time:
            dt = datetime.fromisoformat(lesson['datetime'])
            msg_parts.append(
                f"• `{lesson['id']}` - {lesson.get('name', '韓文課')} | "
                f"{dt.strftime('%Y-%m-%d %H:%M')}"
            )

    # 改課記錄
    modifications = bot.lessons.get('modifications', [])
    if modifications:
        msg_parts.append("\n**🔄 改課記錄：**")
        for mod in modifications:
            dt = datetime.fromisoformat(mod['new_datetime'])
            msg_parts.append(
                f"• `{mod['id']}` - {mod.get('name', '韓文課')} | "
                f"改至 {dt.strftime('%Y-%m-%d %H:%M')}"
            )

    if not msg_parts:
        await interaction.response.send_message("目前沒有設定任何課程")
    else:
        await interaction.response.send_message("\n".join(msg_parts))


@bot.tree.command(name="delete_lesson", description="刪除課程")
@app_commands.describe(lesson_id="課程 ID (使用 /list_lessons 查看)")
async def delete_lesson(interaction: discord.Interaction, lesson_id: str):
    deleted = False

    # 從週期性課程中刪除
    for i, lesson in enumerate(bot.lessons.get('recurring', [])):
        if lesson['id'] == lesson_id:
            bot.lessons['recurring'].pop(i)
            deleted = True
            try:
                bot.scheduler.remove_job(f'recurring_{lesson_id}')
            except:
                pass
            break

    # 從單次課程中刪除
    if not deleted:
        for i, lesson in enumerate(bot.lessons.get('one_time', [])):
            if lesson['id'] == lesson_id:
                bot.lessons['one_time'].pop(i)
                deleted = True
                try:
                    bot.scheduler.remove_job(f'onetime_{lesson_id}')
                except:
                    pass
                break

    # 從改課記錄中刪除
    if not deleted:
        for i, mod in enumerate(bot.lessons.get('modifications', [])):
            if mod['id'] == lesson_id:
                bot.lessons['modifications'].pop(i)
                deleted = True
                try:
                    bot.scheduler.remove_job(f'mod_{lesson_id}')
                except:
                    pass
                break

    if deleted:
        bot.save_lessons()
        await interaction.response.send_message(f"已刪除課程 `{lesson_id}`")
    else:
        await interaction.response.send_message(f"找不到 ID 為 `{lesson_id}` 的課程")


@bot.tree.command(name="set_reminder", description="設定提醒時間")
@app_commands.describe(minutes="上課前幾分鐘提醒")
async def set_reminder(interaction: discord.Interaction, minutes: int):
    if minutes < 1 or minutes > 1440:
        await interaction.response.send_message("提醒時間必須在 1 到 1440 分鐘之間")
        return

    bot.reminder_minutes = minutes
    bot.lessons['reminder_minutes'] = minutes
    bot.save_lessons()

    # 重新設定所有提醒
    await bot.schedule_all_reminders()

    await interaction.response.send_message(f"已設定提醒時間為上課前 **{minutes}** 分鐘")


@bot.tree.command(name="set_channel", description="設定提醒頻道（在要發送提醒的頻道使用此指令）")
async def set_channel(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    channel_name = interaction.channel.name if hasattr(interaction.channel, 'name') else '此頻道'

    bot.lessons['channel_id'] = channel_id
    bot.save_lessons()

    await interaction.response.send_message(f"已設定提醒頻道為：**#{channel_name}**\n提醒訊息將發送到此頻道")


@bot.tree.command(name="add_target", description="新增提醒對象")
@app_commands.describe(user="要新增的提醒對象")
async def add_target(interaction: discord.Interaction, user: discord.User):
    targets = bot.lessons.get('targets', [])

    # 檢查是否已存在
    for t in targets:
        if t['id'] == user.id:
            await interaction.response.send_message(f"**{user.display_name}** 已經是提醒對象了")
            return

    targets.append({
        'id': user.id,
        'name': user.display_name
    })
    bot.lessons['targets'] = targets
    bot.save_lessons()

    await interaction.response.send_message(f"已新增提醒對象：**{user.display_name}**")


@bot.tree.command(name="remove_target", description="移除提醒對象")
@app_commands.describe(user="要移除的提醒對象")
async def remove_target(interaction: discord.Interaction, user: discord.User):
    targets = bot.lessons.get('targets', [])

    for i, t in enumerate(targets):
        if t['id'] == user.id:
            targets.pop(i)
            bot.lessons['targets'] = targets
            bot.save_lessons()
            await interaction.response.send_message(f"已移除提醒對象：**{user.display_name}**")
            return

    await interaction.response.send_message(f"**{user.display_name}** 不在提醒對象中")


@bot.tree.command(name="list_targets", description="列出所有提醒對象")
async def list_targets(interaction: discord.Interaction):
    targets = bot.lessons.get('targets', [])

    if not targets:
        await interaction.response.send_message("目前沒有設定提醒對象\n使用 `/add_target` 新增")
        return

    msg_parts = ["**📢 提醒對象列表：**"]
    for t in targets:
        msg_parts.append(f"• <@{t['id']}> ({t['name']})")

    await interaction.response.send_message("\n".join(msg_parts))


@bot.tree.command(name="set_lesson_target", description="為特定課程設定提醒對象")
@app_commands.describe(
    lesson_id="課程 ID (使用 /list_lessons 查看)",
    user="要新增的提醒對象"
)
async def set_lesson_target(interaction: discord.Interaction, lesson_id: str, user: discord.User):
    found = False

    # 查找並更新課程
    for lesson in bot.lessons.get('recurring', []):
        if lesson['id'] == lesson_id:
            if 'targets' not in lesson:
                lesson['targets'] = []
            # 檢查是否已存在
            for t in lesson['targets']:
                if t['id'] == user.id:
                    await interaction.response.send_message(f"**{user.display_name}** 已經是該課程的提醒對象了")
                    return
            lesson['targets'].append({'id': user.id, 'name': user.display_name})
            found = True
            break

    if not found:
        for lesson in bot.lessons.get('one_time', []):
            if lesson['id'] == lesson_id:
                if 'targets' not in lesson:
                    lesson['targets'] = []
                for t in lesson['targets']:
                    if t['id'] == user.id:
                        await interaction.response.send_message(f"**{user.display_name}** 已經是該課程的提醒對象了")
                        return
                lesson['targets'].append({'id': user.id, 'name': user.display_name})
                found = True
                break

    if found:
        bot.save_lessons()
        await interaction.response.send_message(f"已為課程 `{lesson_id}` 新增提醒對象：**{user.display_name}**")
    else:
        await interaction.response.send_message(f"找不到 ID 為 `{lesson_id}` 的課程")


@bot.tree.command(name="clear_lesson_targets", description="清除課程的特定提醒對象（改用全域設定）")
@app_commands.describe(lesson_id="課程 ID (使用 /list_lessons 查看)")
async def clear_lesson_targets(interaction: discord.Interaction, lesson_id: str):
    found = False

    for lesson in bot.lessons.get('recurring', []):
        if lesson['id'] == lesson_id:
            lesson['targets'] = []
            found = True
            break

    if not found:
        for lesson in bot.lessons.get('one_time', []):
            if lesson['id'] == lesson_id:
                lesson['targets'] = []
                found = True
                break

    if found:
        bot.save_lessons()
        await interaction.response.send_message(f"已清除課程 `{lesson_id}` 的特定提醒對象，將使用全域設定")
    else:
        await interaction.response.send_message(f"找不到 ID 為 `{lesson_id}` 的課程")


@bot.tree.command(name="toggle_early_reminder", description="開啟/關閉提前提醒（上課前X分鐘）")
async def toggle_early_reminder(interaction: discord.Interaction):
    bot.early_reminder_enabled = not bot.early_reminder_enabled
    bot.lessons['early_reminder_enabled'] = bot.early_reminder_enabled
    bot.save_lessons()
    status = "開啟" if bot.early_reminder_enabled else "關閉"
    minutes = bot.reminder_minutes
    await interaction.response.send_message(f"提前提醒已**{status}**（上課前 {minutes} 分鐘）")


@bot.tree.command(name="toggle_start_reminder", description="開啟/關閉上課時間提醒")
async def toggle_start_reminder(interaction: discord.Interaction):
    bot.start_reminder_enabled = not bot.start_reminder_enabled
    bot.lessons['start_reminder_enabled'] = bot.start_reminder_enabled
    bot.save_lessons()
    status = "開啟" if bot.start_reminder_enabled else "關閉"
    await interaction.response.send_message(f"上課時間提醒已**{status}**")


@bot.tree.command(name="help_tutor", description="顯示使用說明")
async def help_tutor(interaction: discord.Interaction):
    help_text = """**韓文課提醒機器人使用說明**

**📅 新增週期性課程**
`/add_lesson weekday:三 time:20:00 name:數學課`
- weekday: 星期幾 (支援：三、週三、星期三、wed 等)
- time: 時間 (支援：20:00、晚上8點、下午3點 等)
- name: 課程名稱 (選填)

**📌 新增單次課程**
`/add_single_lesson date:2024-03-15 time:15:00 name:補課`
- date: 日期 (格式：YYYY-MM-DD 或 MM-DD)

**🔄 改課**
`/reschedule lesson_id:abc123 new_date:03-20 new_time:20:00`
- 先用 /list_lessons 查看課程 ID

**📢 提醒對象管理**
- `/add_target user:@某人` - 新增提醒對象
- `/remove_target user:@某人` - 移除提醒對象
- `/list_targets` - 列出所有提醒對象
- `/set_lesson_target lesson_id:xxx user:@某人` - 為特定課程設定對象
- `/clear_lesson_targets lesson_id:xxx` - 清除課程特定對象

**📋 其他指令**
- `/set_channel` - 設定提醒頻道（在目標頻道使用）
- `/list_lessons` - 列出所有課程
- `/delete_lesson lesson_id:xxx` - 刪除課程
- `/set_reminder minutes:30` - 設定提前提醒時間（目前預設30分鐘）

**🔔 提醒開關**
- `/toggle_early_reminder` - 開啟/關閉提前提醒（上課前X分鐘）
- `/toggle_start_reminder` - 開啟/關閉上課時間提醒（準時提醒）

**⏰ 時區**
所有時間皆為台灣時間 (UTC+8)
"""
    await interaction.response.send_message(help_text)


if __name__ == '__main__':
    if not DISCORD_TOKEN:
        print("錯誤：請設定 DISCORD_TOKEN 環境變數")
        exit(1)

    bot.run(DISCORD_TOKEN)
