import os
import asyncio
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile
from openai import AsyncOpenAI

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor

# Загрузка токенов из .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Инициализация OpenRouter
client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# -------------------------------------------------------------------
# Вспомогательный класс для перехвата имени скачанного файла из yt-dlp
# -------------------------------------------------------------------
class FileNameCollectorPP(PostProcessor):
    def __init__(self):
        super().__init__(None)
        self.filenames = []

    def run(self, information):
        self.filenames.append(information.get('filepath'))
        return [], information

# -------------------------------------------------------------------
# Функция скачивания аудио с YouTube по поисковому запросу
# -------------------------------------------------------------------
async def download_yt_audio(query: str):
    """Ищет трек на YouTube, скачивает в MP3 и возвращает путь к файлу"""
    loop = asyncio.get_event_loop()
    filename_collector = FileNameCollectorPP()

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
            # ytsearch1: скачивает первое найденное видео
            ydl.download([f"ytsearch1:{query}"])
        if filename_collector.filenames:
            return filename_collector.filenames[0]
        return None

    # Запуск блокирующего скачивания в отдельном потоке
    return await loop.run_in_executor(None, _download)

# -------------------------------------------------------------------
# Хэндлеры бота
# -------------------------------------------------------------------
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я бот для общения и поиска музыки.\n\n"
        "• Напиши /song <название>, чтобы найти и скачать MP3 с YouTube.\n"
        "• Или просто пиши любой текст — я отвечу через нейросеть."
    )

# Поиск и скачивание музыки по команде /song или /s
@dp.message(Command("song", "s"))
async def handle_music_search(message: Message):
    # Получаем текст после команды
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Укажи название трека. Пример: `/song I Will Survive`", parse_mode="Markdown")
        return

    query = args[1]
    status_msg = await message.answer(f"🔎 Ищу и скачиваю: **{query}**...", parse_mode="Markdown")

    try:
        file_path = await download_yt_audio(query)

        if file_path and os.path.exists(file_path):
            audio_file = FSInputFile(file_path)
            await message.answer_audio(audio=audio_file, caption=f"🎵 {query}")
            await status_msg.delete()

            # Удаляем файл после отправки
            os.remove(file_path)
        else:
            await status_msg.edit_text("Не удалось найти или скачать трек.")

    except Exception as e:
        print(f"Ошибка YT-DLP: {e}")
        await status_msg.edit_text("Произошла ошибка при скачивании трека.")

# Обычное текстовое общение с нейросетью (со стримингом ответа)
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
# Запуск
# -------------------------------------------------------------------
async def main():
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
