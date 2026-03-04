# 課程提醒機器人

Discord 機器人，用於提醒家教課程時間。

## 功能

- 設定週期性課程（例如：每週三晚上 8 點）
- 設定單次課程
- 上課前自動提醒（預設 30 分鐘前）
- 改課功能
- 可調整提醒時間
- **可設定多個提醒對象**（全域或針對特定課程）
- 台灣時區 (UTC+8)

## 安裝

### 1. 建立 Discord Bot

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)
2. 點擊 "New Application" 建立新應用程式
3. 在左側選單點擊 "Bot"
4. 點擊 "Add Bot"
5. 複製 Bot Token（點擊 "Reset Token" 取得）
6. 開啟以下權限：
   - MESSAGE CONTENT INTENT

### 2. 邀請 Bot 到伺服器

1. 在 Developer Portal 左側選單點擊 "OAuth2" > "URL Generator"
2. 在 SCOPES 勾選 `bot` 和 `applications.commands`
3. 在 BOT PERMISSIONS 勾選：
   - Send Messages
   - Mention Everyone
4. 複製產生的 URL，在瀏覽器開啟並選擇伺服器

### 3. 設定環境

```bash
# 安裝依賴
pip install -r requirements.txt

# 複製環境變數範例檔
cp .env.example .env

# 編輯 .env 填入設定
```

編輯 `.env` 檔案：

```
DISCORD_TOKEN=你的_bot_token
DEFAULT_REMINDER_MINUTES=30
```

### 4. 啟動機器人

```bash
python bot.py
```

### 5. 首次設定

啟動後在 Discord 執行以下指令：

```
# 在要發送提醒的頻道設定提醒頻道
/set_channel

# 新增要提醒的對象
/add_target user:@老師
```

## 指令說明

### 新增週期性課程
```
/add_lesson weekday:三 time:20:00 name:數學課
```
- `weekday`: 星期幾（支援：三、週三、星期三、wed、wednesday）
- `time`: 時間（支援：20:00、晚上8點、下午3點）
- `name`: 課程名稱（選填，預設「家教課」）

### 新增單次課程
```
/add_single_lesson date:2024-03-15 time:15:00 name:補課
```
- `date`: 日期（格式：YYYY-MM-DD 或 MM-DD）
- `time`: 時間
- `name`: 課程名稱（選填）

### 改課
```
/reschedule lesson_id:abc123 new_date:03-20 new_time:20:00
```
先使用 `/list_lessons` 查看課程 ID

### 列出所有課程
```
/list_lessons
```

### 刪除課程
```
/delete_lesson lesson_id:abc123
```

### 設定提醒頻道
```
/set_channel
```
在目標頻道執行此指令，提醒訊息將發送到該頻道

### 設定提醒時間
```
/set_reminder minutes:30
```
設定在上課前幾分鐘發送提醒

### 提醒對象管理

**新增全域提醒對象**
```
/add_target user:@老師
```

**移除提醒對象**
```
/remove_target user:@老師
```

**列出所有提醒對象**
```
/list_targets
```

**為特定課程設定提醒對象**
```
/set_lesson_target lesson_id:abc123 user:@學生
```
如果課程有設定特定對象，則只會 tag 該課程的對象，否則使用全域設定

**清除課程特定對象（改用全域設定）**
```
/clear_lesson_targets lesson_id:abc123
```

### 查看說明
```
/help_tutor
```

## 使用範例

```
# 首次設定：在要發送提醒的頻道執行
/set_channel

# 設定提醒對象
/add_target user:@老師
/add_target user:@學生

# 新增每週三晚上 8 點的課
/add_lesson weekday:三 time:晚上8點 name:英文課

# 新增每週日下午 3 點的課
/add_lesson weekday:日 time:下午3點 name:數學課

# 這週日改成晚上 8 點
/reschedule lesson_id:xxx new_date:03-10 new_time:20:00

# 設定提醒時間為 15 分鐘前
/set_reminder minutes:15

# 為某堂課設定特定提醒對象（只 tag 這個人）
/set_lesson_target lesson_id:xxx user:@助教
```

## 資料儲存

課程資料儲存在 `lessons.json` 檔案中，機器人重啟後會自動載入。

## 注意事項

- 所有時間皆為台灣時間 (UTC+8)
- 機器人需要保持運行才能發送提醒
- 建議使用 screen、tmux 或 systemd 讓機器人在背景運行
