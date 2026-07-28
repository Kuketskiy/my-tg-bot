import os
import asyncio
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, BotCommand
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from openai import AsyncOpenAI

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor

# Загрузка переменных окружения из .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Инициализация клиента OpenRouter (DeepSeek)
client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# -------------------------------------------------------------------
# Состояния бота (FSM)
# -------------------------------------------------------------------
class DownloadState(StatesGroup):
    waiting_for_song_query = State()
    waiting_for_video_query = State()

# -------------------------------------------------------------------
# Класс для перехвата имени файла из yt-dlp
# -------------------------------------------------------------------
class FileNameCollectorPP(PostProcessor):
    def __init__(self):
        super().__init__(None)
        self.filenames = []

    def run(self, information):
        self.filenames.append(information.get('filepath'))
        return [], information

# -------------------------------------------------------------------
# Функция скачивания с YouTube
# -------------------------------------------------------------------
async def download_yt_content(query: str, is_video: bool = False):
    """Ищет и скачивает трек (MP3) или видео (MP4) с YouTube"""
    loop = asyncio.get_event_loop()
    filename_collector = FileNameCollectorPP()

    # Базовые настройки yt-dlp
    base_opts = {
        'noplaylist': True,
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'quiet': True,
        'ignoreerrors': True,
        # Лимит 50 МБ (ограничение Telegram API на отправку файлов ботом)
        'max_filesize': 50 * 1024 * 1024,
        # Пропуск видео с возрастными ограничениями (18+)
        'match_filter': lambda info, incomplete: None if not info.get('age_limit') else 'Skip age restricted',
    }

    if is_video:
        ydl_opts = {
            **base_opts,
            'format': 'bestvideo[ext=mp4][filesize<=50M]+bestaudio[ext=m4a]/best[ext=mp4][filesize<=50M]/best',
        }
    else:
        ydl_opts = {
            **base_opts,
            'format': 'bestaudio/best',
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
# Регистрация меню команд в Telegram
# -------------------------------------------------------------------
async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="🚀 Перезапустить бота"),
        BotCommand(command="song", description="🎵 Скачать песню (MP3)"),
        BotCommand(command="video", description="🎬 Скачать видео (MP4)"),
        BotCommand(command="help", description="ℹ️ Инструкция"),
    ]
    await bot.set_my_commands(commands)

# -------------------------------------------------------------------
# Хэндлеры команд
# -------------------------------------------------------------------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 **Привет! Я твой универсальный бот.**\n\n"
        "• Нажми `/song` или `/video` в меню, чтобы найти медиа.\n"
        "• Или просто напиши мне любой вопрос для ИИ!"
    )

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📌 **Как пользоваться:**\n\n"
        "1. Нажми `/song` или `/video` в меню (или введи команду).\n"
        "2. Бот попросит название — отправь его следующим сообщением.\n"
        "3. Любое общение без команд обрабатывается нейросетью."
    )

# --- Инициализация команды /song ---
@dp.message(Command("song"))
async def cmd_song_init(message: Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        # Если написали сразу: /song Queen
        await process_song_download(message, args[1], state)
    else:
        # Если просто кликнули по /song в меню
        await state.set_state(DownloadState.waiting_for_song_query)
        await message.answer("🎵 **Введи название песни или исполнителя:**")

# --- Инициализация команды /video ---
@dp.message(Command("video"))
async def cmd_video_init(message: Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        # Если написали сразу: /video Котики
        await process_video_download(message, args[1], state)
    else:
        # Если просто кликнули по /video в меню
        await state.set_state(DownloadState.waiting_for_video_query)
        await message.answer("🎬 **Введи название или тему видео:**")

# -------------------------------------------------------------------
# Перехват ввода после выбора команд из меню
# -------------------------------------------------------------------
@dp.message(DownloadState.waiting_for_song_query)
async def handle_song_input(message: Message, state: FSMContext):
    await process_song_download(message, message.text, state)

@dp.message(DownloadState.waiting_for_video_query)
async def handle_video_input(message: Message, state: FSMContext):
    await process_video_download(message, message.text, state)

# -------------------------------------------------------------------
# Логика скачивания и отправки файлов
# -------------------------------------------------------------------
async def process_song_download(message: Message, query: str, state: FSMContext):
    await state.clear()
    status_msg = await message.answer(f"🔎 Ищу и скачиваю песню: **{query}**...", parse_mode="Markdown")
    try:
        file_path = await download_yt_content(query, is_video=False)

        if file_path and os.path.exists(file_path):
            audio_file = FSInputFile(file_path)
            await message.answer_audio(audio=audio_file, caption=f"🎵 {query}")
            await status_msg.delete()
            os.remove(file_path)
        else:
            await status_msg.edit_text("Не удалось найти трек или файл слишком большой (>50 МБ).")
    except Exception as e:
        print(f"Ошибка YT-DLP: {e}")
        await status_msg.edit_text("Произошла ошибка при скачивании трека.")

async def process_video_download(message: Message, query: str, state: FSMContext):
    await state.clear()
    status_msg = await message.answer(f"🔎 Ищу и скачиваю видео: **{query}**...", parse_mode="Markdown")
    try:
        file_path = await download_yt_content(query, is_video=True)

        if file_path and os.path.exists(file_path):
            video_file = FSInputFile(file_path)
            await message.answer_video(video=video_file, caption=f"🎬 {query}")
            await status_msg.delete()
            os.remove(file_path)
        else:
            await status_msg.edit_text("Не удалось найти видео или файл слишком большой (>50 МБ).")
    except Exception as e:
        print(f"Ошибка YT-DLP: {e}")
        await status_msg.edit_text("Произошла ошибка при скачивании видео.")

# -------------------------------------------------------------------
# Обычный диалог с нейросетью (DeepSeek)
# -------------------------------------------------------------------
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
# Главная функция запуска
# -------------------------------------------------------------------
async def main():
    if not os.path.exists("downloads"):
        os.makedirs("downloads")

    await set_bot_commands()
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
