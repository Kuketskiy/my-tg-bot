import os
import asyncio
import subprocess
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from openai import AsyncOpenAI
import speech_recognition as sr
from pydub import AudioSegment
from shazamio import Shazam

# Загрузка переменных окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

shazam = Shazam()

# -------------------------------------------------------------------
# Функция распознавания музыки через Shazamio
# -------------------------------------------------------------------
async def recognize_music(file_path: str):
    """Распознает трек через Shazam API"""
    try:
        out = await shazam.recognize(file_path)
        track = out.get("track")
        if not track:
            return None

        title = track.get("title", "Неизвестное название")
        subtitle = track.get("subtitle", "Неизвестный исполнитель")
        images = track.get("images", {})
        cover_url = images.get("coverarthq") or images.get("coverart")

        return {
            "title": title,
            "subtitle": subtitle,
            "cover_url": cover_url
        }
    except Exception as e:
        print(f"Ошибка Shazamio: {repr(e)}")
        return None

# -------------------------------------------------------------------
# Функция распознавания голоса в текст
# -------------------------------------------------------------------
async def transcribe_voice(file_path: str) -> str:
    wav_path = file_path + ".wav"
    try:
        sound = AudioSegment.from_file(file_path)
        sound = sound.set_frame_rate(16000).set_channels(1)
        sound.export(wav_path, format="wav")

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="ru-RU")
            return text
    except Exception as e:
        print(f"Ошибка распознавания речи: {e}")
        return ""
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

# -------------------------------------------------------------------
# Хэндлеры бота
# -------------------------------------------------------------------
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("Привет! Я готов к работе. Отправляй мне текстовые сообщения, голосовые или музыку для распознавания!")

@dp.message(F.voice | F.audio | F.video_note)
async def handle_audio(message: Message):
    status_msg = await message.answer("🔎 Анализирую аудио...")
    
    file_id = message.voice.file_id if message.voice else (
        message.audio.file_id if message.audio else message.video_note.file_id
    )
    file = await bot.get_file(file_id)
    file_path = f"temp_{file_id}"
    await bot.download_file(file.file_path, file_path)

    try:
        # 1. Пробуем распознать музыку
        music_info = await recognize_music(file_path)
        
        if music_info:
            res_text = f"🎵 **Найден трек!**\n\n📌 **Название:** {music_info['title']}\n👤 **Исполнитель:** {music_info['subtitle']}"
            if music_info.get("cover_url"):
                await message.answer_photo(photo=music_info["cover_url"], caption=res_text, parse_mode="Markdown")
            else:
                await message.answer(res_text, parse_mode="Markdown")
            await status_msg.delete()
            return

        # 2. Если это голосовое и не музыка — переводим в текст
        if message.voice or message.video_note:
            text = await transcribe_voice(file_path)
            if text:
                await status_msg.edit_text(f"🗣 **Вы сказали:** _{text}_\n\nДумаю над ответом...", parse_mode="Markdown")
                response = await client.chat.completions.create(
                    model="deepseek/deepseek-chat",
                    messages=[{"role": "user", "content": text}]
                )
                await message.answer(response.choices[0].message.content)
            else:
                await status_msg.edit_text("Не удалось распознать речь или музыка не найдена.")
        else:
            await status_msg.edit_text("Музыка в файле не найдена.")

    except Exception as e:
        print(f"Ошибка при обработке аудио: {e}")
        await status_msg.edit_text("Произошла ошибка при обработке файла.")

    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@dp.message(F.text)
async def handle_text(message: Message):
    try:
        response = await client.chat.completions.create(
            model="deepseek/deepseek-chat",
            messages=[{"role": "user", "content": message.text}]
        )
        await message.answer(response.choices[0].message.content)
    except Exception as e:
        await message.answer(f"Ошибка при запросе к AI: {e}")

async def main():
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
