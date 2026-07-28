import os
import asyncio
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, BotCommand
from openai import AsyncOpenAI

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor

# Загрузка токенов из .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# -------------------------------------------------------------------
# Класс для перехвата имени скачанного файла из yt-dlp
# -------------------------------------------------------------------
class FileNameCollectorPP(PostProcessor):
    def __init__(self):
        super().__init__(None)
        self.filenames = []

    def run(self, information):
        self.filenames.append(information.get('filepath'))
        return [], information

# -------------------------------------------------------------------
# Функция скачивания аудио/видео с YouTube
# -------------------------------------------------------------------
async def download_yt_content(query: str, is_video: bool = False):
    """Ищет и скачивает трек (MP3) или видео (MP4) с YouTube"""
    loop = asyncio.get_event_loop()
    filename_collector = FileNameCollectorPP()

    if is_video:
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'noplaylist': True,
            'outtmpl': 'downloads/%(title)s.%(ext)s',
            'quiet': True,
        }
    else:
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'outtmpl': 'downloads/%(title)s.%(ext)s',
            'quiet': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }

    def _download():
        with YoutubeDL(ydl_opts) as ydl:
            ydl.add_post_processor(filename_collector)
            ydl.download([f"ytsearch1:{query}"])
        if filename_collector.filenames:
            return filename_collector.filenames[0]
        return None

    return await loop.run_in_executor(None, _download)

# -------------------------------------------------------------------
# Настройка синей кнопки «Меню» в Telegram
# -------------------------------------------------------------------
async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="🚀 Перезапустить бота"),
        BotCommand(command="song", description="🎵 Скачать песню (MP3)"),
        BotCommand(command="video", description="🎬 Скачать видео (MP4)"),
        BotCommand(command="help", description="ℹ️ Инструкция по командам"),
    ]
    await bot.set_my_commands(commands)

# -------------------------------------------------------------------
# Хэндлеры бота
# -------------------------------------------------------------------
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 **Привет! Я твой универсальный бот.**\n\n"
        "Воспользуйся кнопкой **«Меню»** слева от поля ввода или введи символ `/` для просмотра всех команд.\n\n"
        "• `/song <название>` — скачать аудио с YouTube\n"
        "• `/video <название>` — скачать видео с YouTube\n"
        "• Или просто напиши мне любой вопрос, и я отвечу!"
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📌 **Как пользоваться ботом:**\n\n"
        "1. **Для поиска музыки:** отправь `/song Название трека` (например: `/song Queen Bohemian Rhapsody`)\n"
        "2. **Для поиска видео:** отправь `/video Название ролике` (например: `/video funny cats`)\n"
        "3. **Для общения с ИИ:** просто отправляй любой текст без команд."
    )

# Поиск и скачивание МУЗЫКИ (/song)
@dp.message(Command("song"))
async def handle_song_cmd(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("⚠️ Укажи название трека!\nПример: `/song I Will Survive`", parse_mode="Markdown")
        return

    query = args[1]
    status_msg = await message.answer(f"🔎 Ищу и скачиваю песню: **{query}**...", parse_mode="Markdown")

    try:
        file_path = await download_yt_content(query, is_video=False)

        if file_path and os.path.exists(file_path):
            audio_file = FSInputFile(file_path)
            await message.answer_audio(audio=audio_file, caption=f"🎵 {query}")
            await status_msg.delete()
            os.remove(file_path)
        else:
            await status_msg.edit_text("Не удалось найти или скачать трек.")
    except Exception as e:
        print(f"Ошибка YT-DLP: {e}")
        await status_msg.edit_text("Произошла ошибка при скачивании трека.")

# Поиск и скачивание ВИДЕО (/video)
@dp.message(Command("video"))
async def handle_video_cmd(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("⚠️ Укажи название видео!\nПример: `/video смешные коты`", parse_mode="Markdown")
        return

    query = args[1]
    status_msg = await message.answer(f"🔎 Ищу и скачиваю видео: **{query}**...", parse_mode="Markdown")

    try:
        file_path = await download_yt_content(query, is_video=True)

        if file_path and os.path.exists(file_path):
            video_file = FSInputFile(file_path)
            await message.answer_video(video=video_file, caption=f"🎬 {query}")
            await status_msg.delete()
            os.remove(file_path)
        else:
            await status_msg.edit_text("Не удалось найти или скачать видео.")
    except Exception as e:
        print(f"Ошибка YT-DLP: {e}")
        await status_msg.edit_text("Произошла ошибка при скачивании видео.")

# Обычное текстовое общение с ИИ
@dp.message(F.text)
async def handle_text(message: Message):
    try:
        await bot.send_chat_action(message.chat.id, "typing")

        response_stream = await client.chat.completions.create(
            model="deepseek/deepseek-chat",
            messages=[{"role": "user", "content": message.text}],
            stream=True
        )

        sent_message = None
        full_text = ""
        last_updated_len = 0

        async for chunk in response_stream:
            content = chunk.choices[0].delta.content
            if content:
                full_text += content
                if not sent_message:
                    sent_message = await message.answer(full_text)
                    last_updated_len = len(full_text)
                elif len(full_text) - last_updated_len > 20:
                    await sent_message.edit_text(full_text)
                    last_updated_len = len(full_text)

        if sent_message and len(full_text) != last_updated_len:
            await sent_message.edit_text(full_text)

    except Exception as e:
        await message.answer(f"Ошибка при запросе к AI: {e}")

# -------------------------------------------------------------------
# Запуск бота
# -------------------------------------------------------------------
async def main():
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    
    # Регистрируем меню команд в Telegram при запуске
    await set_bot_commands()
    
    print("Бот успешно запущен! Меню команд обновлено.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
