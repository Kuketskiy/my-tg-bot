import os
import asyncio
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, BotCommand, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
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
# Перехватчик имен файлов yt-dlp
# -------------------------------------------------------------------
class FileNameCollectorPP(PostProcessor):
    def __init__(self):
        super().__init__(None)
        self.filenames = []

    def run(self, information):
        self.filenames.append(information.get('filepath'))
        return [], information

# -------------------------------------------------------------------
# Функция скачивания медиа
# -------------------------------------------------------------------
async def download_media(target: str, quality: str = None, is_audio: bool = False):
    loop = asyncio.get_event_loop()
    filename_collector = FileNameCollectorPP()

    base_opts = {
        'noplaylist': True,
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'quiet': True,
        'ignoreerrors': True,
        'max_filesize': 50 * 1024 * 1024, # Лимит Telegram
        'match_filter': lambda info, incomplete: None if not info.get('age_limit') else 'Skip age restricted',
    }

    if is_audio:
        ydl_opts = {
            **base_opts,
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }
    else:
        # Выбор качества видео по высоте (height)
        height = quality if quality else "720"
        fmt_str = f'bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={height}][ext=mp4]/best'
        ydl_opts = {
            **base_opts,
            'format': fmt_str,
        }

    # Если передана не ссылка, а поисковый запрос
    url_or_search = target if target.startswith("http") else f"ytsearch1:{target}"

    def _download():
        with YoutubeDL(ydl_opts) as ydl:
            ydl.add_post_processor(filename_collector)
            ydl.download([url_or_search])
        if filename_collector.filenames:
            return filename_collector.filenames[0]
        return None

    return await loop.run_in_executor(None, _download)

# -------------------------------------------------------------------
# Клавиатура выбора качества
# -------------------------------------------------------------------
def get_quality_keyboard(query: str):
    builder = InlineKeyboardBuilder()
    
    # Резаем query, чтобы не превысить лимит callback_data (64 байта)
    short_q = query[:30]
    
    builder.button(text="🎬 1080p", callback_data=f"vid:1080:{short_q}")
    builder.button(text="🎬 720p", callback_data=f"vid:720:{short_q}")
    builder.button(text="🎬 480p", callback_data=f"vid:480:{short_q}")
    builder.button(text="🎬 360p", callback_data=f"vid:360:{short_q}")
    builder.button(text="🎵 Аудио (MP3)", callback_data=f"aud:mp3:{short_q}")
    
    builder.adjust(2)
    return builder.as_markup()

# -------------------------------------------------------------------
# Настройка меню команд
# -------------------------------------------------------------------
async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="🚀 Перезапустить бота"),
        BotCommand(command="song", description="🎵 Скачать песню (MP3)"),
        BotCommand(command="video", description="🎬 Скачать видео (с выбором качества)"),
        BotCommand(command="help", description="ℹ️ Инструкция"),
    ]
    await bot.set_my_commands(commands)

# -------------------------------------------------------------------
# Перехватчик ошибок блокировки бота
# -------------------------------------------------------------------
@dp.error()
async def global_error_handler(event: types.ErrorEvent):
    if isinstance(event.exception, TelegramForbiddenError):
        print("Запрос пропущен: бот заблокирован пользователем.")
        return True

# -------------------------------------------------------------------
# Базовые команды
# -------------------------------------------------------------------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 **Привет! Я бот для медиа и общения.**\n\n"
        "• `/video` — Скачать видео с YouTube с выбором качества.\n"
        "• `/song` — Скачать музыку в MP3.\n"
        "• Или просто напиши любой вопрос, и ответит **DeepSeek-V3**."
    )

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📌 **Инструкция:**\n\n"
        "1. Нажми `/video` и введи название или ссылку → выбери качество на кнопках.\n"
        "2. Нажми `/song` и введи название → бот сразу пришлет MP3.\n"
        "3. Любой другой текст — диалог с ИИ."
    )

@dp.message(Command("song"))
async def cmd_song_init(message: Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        await start_song_download(message, args[1], state)
    else:
        await state.set_state(DownloadState.waiting_for_song_query)
        await message.answer("🎵 **Введи название песни или ссылку:**")

@dp.message(Command("video"))
async def cmd_video_init(message: Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        await show_quality_options(message, args[1], state)
    else:
        await state.set_state(DownloadState.waiting_for_video_query)
        await message.answer("🎬 **Введи название видео или ссылку:**")

# -------------------------------------------------------------------
# Обработка ввода (FSM)
# -------------------------------------------------------------------
@dp.message(DownloadState.waiting_for_song_query)
async def handle_song_input(message: Message, state: FSMContext):
    await start_song_download(message, message.text, state)

@dp.message(DownloadState.waiting_for_video_query)
async def handle_video_input(message: Message, state: FSMContext):
    await show_quality_options(message, message.text, state)

async def show_quality_options(message: Message, query: str, state: FSMContext):
    await state.clear()
    kb = get_quality_keyboard(query)
    await message.answer(f"🎬 Выбери качество для скачивания:\n**{query}**", reply_markup=kb, parse_mode="Markdown")

async def start_song_download(message: Message, query: str, state: FSMContext):
    await state.clear()
    status_msg = await message.answer(f"🔎 Ищу и скачиваю трек: **{query}**...", parse_mode="Markdown")
    await process_download(status_msg, query, is_audio=True)

# -------------------------------------------------------------------
# Обработка нажатий на кнопки качества
# -------------------------------------------------------------------
@dp.callback_query(F.data.startswith("vid:"))
async def cb_download_video(callback: CallbackQuery):
    _, quality, query = callback.data.split(":", 2)
    await callback.answer(f"Выбрано {quality}p")
    
    status_msg = await callback.message.edit_text(
        f"⏳ Скачиваю видео (**{quality}p**): `{query}`...", 
        reply_markup=None,
        parse_mode="Markdown"
    )
    await process_download(status_msg, query, quality=quality, is_audio=False)

@dp.callback_query(F.data.startswith("aud:"))
async def cb_download_audio(callback: CallbackQuery):
    _, _, query = callback.data.split(":", 2)
    await callback.answer("Скачиваю MP3")
    
    status_msg = await callback.message.edit_text(
        f"⏳ Скачиваю аудио: `{query}`...", 
        reply_markup=None,
        parse_mode="Markdown"
    )
    await process_download(status_msg, query, is_audio=True)

# -------------------------------------------------------------------
# Логика загрузки и отправки в Telegram
# -------------------------------------------------------------------
async def process_download(status_msg: Message, query: str, quality: str = None, is_audio: bool = False):
    try:
        file_path = await download_media(query, quality=quality, is_audio=is_audio)
        
        if file_path and os.path.exists(file_path):
            input_file = FSInputFile(file_path)
            
            if is_audio:
                await status_msg.chat.do("upload_voice")
                await status_msg.answer_audio(audio=input_file, caption=f"🎵 {query}")
            else:
                await status_msg.chat.do("upload_video")
                await status_msg.answer_video(video=input_file, caption=f"🎬 {query} ({quality}p)")

            await status_msg.delete()
            os.remove(file_path)
        else:
            await status_msg.edit_text("❌ Файл не найден, заблокирован 18+ или превышает лимит Telegram (50 МБ).")
    except TelegramForbiddenError:
        pass
    except Exception as e:
        print(f"Ошибка при скачивании: {e}")
        try:
            await status_msg.edit_text("❌ Произошла ошибка при скачивании файла.")
        except TelegramForbiddenError:
            pass

# -------------------------------------------------------------------
# Чат с DeepSeek-V3
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
                elif len(full_text) - last_updated_len > 25:
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
# Запуск бота
# -------------------------------------------------------------------
async def main():
    if not os.path.exists("downloads"):
        os.makedirs("downloads")

    await set_bot_commands()
    print("🚀 Бот с выбором качества и защитой запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
