import os
import asyncio
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, BotCommand
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError
from openai import AsyncOpenAI

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor

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
# FSM Состояния
# -------------------------------------------------------------------
class DownloadState(StatesGroup):
    waiting_for_song_query = State()
    waiting_for_video_query = State()

# -------------------------------------------------------------------
# Перехватчик файлов yt-dlp
# -------------------------------------------------------------------
class FileNameCollectorPP(PostProcessor):
    def __init__(self):
        super().__init__(None)
        self.filenames = []

    def run(self, information):
        self.filenames.append(information.get('filepath'))
        return [], information

# -------------------------------------------------------------------
# Скачивание медиа
# -------------------------------------------------------------------
async def download_yt_content(query: str, is_video: bool = False):
    loop = asyncio.get_event_loop()
    filename_collector = FileNameCollectorPP()

    base_opts = {
        'noplaylist': True,
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'quiet': True,
        'ignoreerrors': True,
        'max_filesize': 50 * 1024 * 1024, # Лимит 50 МБ для Telegram
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
# Установка меню
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
# Глобальный перехватчик ошибок блокировки
# -------------------------------------------------------------------
@dp.error()
async def global_error_handler(event: types.ErrorEvent):
    if isinstance(event.exception, TelegramForbiddenError):
        print("Запрос пропущен: бот заблокирован пользователем.")
        return True

# -------------------------------------------------------------------
# Команды
# -------------------------------------------------------------------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 **Привет! Я твой универсальный бот.**\n\n"
        "• Выбери в меню `/song` или `/video`, и я спрошу название.\n"
        "• Или просто напиши мне любой вопрос для ИИ."
    )

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📌 **Как пользоваться:**\n\n"
        "1. Нажми `/song` или `/video` в меню.\n"
        "2. Бот попросит ввести название — отправь его текстом.\n"
        "3. Любые другие сообщения отправляются в ИИ."
    )

@dp.message(Command("song"))
async def cmd_song_init(message: Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        await process_song_download(message, args[1], state)
    else:
        await state.set_state(DownloadState.waiting_for_song_query)
        await message.answer("🎵 **Введи название песни или исполнителя:**")

@dp.message(Command("video"))
async def cmd_video_init(message: Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        await process_video_download(message, args[1], state)
    else:
        await state.set_state(DownloadState.waiting_for_video_query)
        await message.answer("🎬 **Введи название или тему видео:**")

# -------------------------------------------------------------------
# Обработка ввода для скачивания
# -------------------------------------------------------------------
@dp.message(DownloadState.waiting_for_song_query)
async def handle_song_input(message: Message, state: FSMContext):
    await process_song_download(message, message.text, state)

@dp.message(DownloadState.waiting_for_video_query)
async def handle_video_input(message: Message, state: FSMContext):
    await process_video_download(message, message.text, state)

# -------------------------------------------------------------------
# Скачивание и отправка
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
            await status_msg.edit_text("Не удалось найти трек или файл превышает 50 МБ.")
    except TelegramForbiddenError:
        pass
    except Exception as e:
        print(f"Ошибка YT-DLP: {e}")
        try:
            await status_msg.edit_text("Произошла ошибка при скачивании трека.")
        except TelegramForbiddenError:
            pass

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
            await status_msg.edit_text("Не удалось найти видео или файл превышает 50 МБ.")
    except TelegramForbiddenError:
        pass
    except Exception as e:
        print(f"Ошибка YT-DLP: {e}")
        try:
            await status_msg.edit_text("Произошла ошибка при скачивании видео.")
        except TelegramForbiddenError:
            pass

# -------------------------------------------------------------------
# ИИ чат (DeepSeek)
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

    except TelegramForbiddenError:
        print(f"Пользователь {message.from_user.id} заблокировал бота.")
    except TelegramAPIError as e:
        print(f"Ошибка Telegram API: {e}")
    except Exception as e:
        print(f"Ошибка AI: {e}")
        try:
            await message.answer("Произошла ошибка при обращении к ИИ.")
        except TelegramForbiddenError:
            pass

# -------------------------------------------------------------------
# Запуск
# -------------------------------------------------------------------
async def main():
    if not os.path.exists("downloads"):
        os.makedirs("downloads")

    await set_bot_commands()
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
