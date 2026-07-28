import os
import json
import asyncio
import subprocess
import aiohttp
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from openai import AsyncOpenAI
import speech_recognition as sr
from pydub import AudioSegment

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

# -------------------------------------------------------------------
# Функция распознавания музыки (Прямой HTTP-запрос к Shazam)
# -------------------------------------------------------------------
async def recognize_music(file_path: str):
    """Конвертирует аудио в моно WAV 16kHz и отправляет в Shazam API"""
    wav_path = file_path + "_shazam.wav"
    try:
        # Конвертируем входной файл в WAV (16000 Hz, mono) через ffmpeg
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", file_path,
            "-ar", "16000", "-ac", "1", "-f", "wav", wav_path,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        await proc.communicate()

        if not os.path.exists(wav_path):
            return None

        async with aiohttp.ClientSession() as session:
            with open(wav_path, "rb") as f:
                # Читаем сэмпл файла
                audio_bytes = f.read(500000)

            url = "https://amp.shazam.com/discovery/v5/ru/RU/android/-/tag/sample"
            headers = {
                "User-Agent": "Shazam/11.7.0 (Android; arm64-v8a)",
                "Content-Type": "application/octet-stream"
            }
            
            async with session.post(url, data=audio_bytes, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        track = data.get("track")
        if not track:
            return None

        return {
            "title": track.get("title", "Неизвестное название"),
            "subtitle": track.get("subtitle", "Неизвестный исполнитель"),
            "cover_url": track.get("images", {}).get("coverarthq") or track.get("images", {}).get("coverart")
        }
    except Exception as e:
        print(f"Ошибка Shazam: {e}")
        return None
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

# -------------------------------------------------------------------
# Функция распознавания голоса в текст (Google Speech Recognition)
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
            return recognizer.recognize_google(audio_data, language="ru-RU")
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

# Обработка голосовых сообщений, аудиофайлов и видео-записок
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
        # 1. Сначала пробуем распознать как музыку
        music_info = await recognize_music(file_path)
        
        if music_info:
            res_text = f"🎵 **Найден трек!**\n\n📌 **Название:** {music_info['title']}\n👤 **Исполнитель:** {music_info['subtitle']}"
            if music_info.get("cover_url"):
                await message.answer_photo(photo=music_info["cover_url"], caption=res_text, parse_mode="Markdown")
            else:
                await message.answer(res_text, parse_mode="Markdown")
            await status_msg.delete()
            return

        # 2. Если трек не найден и это голосовое — переводим в текст для нейросети
        if message.voice or message.video_note:
            text = await transcribe_voice(file_path)
            if text:
                await status_msg.edit_text(f"🗣 **Вы сказали:** _{text}_\n\nГенерирую ответ...", parse_mode="Markdown")
                await bot.send_chat_action(message.chat.id, "typing")
                
                # Запрос к нейросети со стримингом
                response_stream = await client.chat.completions.create(
                    model="deepseek/deepseek-chat",
                    messages=[{"role": "user", "content": text}],
                    stream=True
                )
                
                full_text = ""
                last_len = 0
                async for chunk in response_stream:
                    content = chunk.choices[0].delta.content
                    if content:
                        full_text += content
                        if len(full_text) - last_len > 25:
                            await status_msg.edit_text(f"🗣 **Вы сказали:** _{text}_\n\n{full_text}")
                            last_len = len(full_text)
                
                if len(full_text) != last_len:
                    await status_msg.edit_text(f"🗣 **Вы сказали:** _{text}_\n\n{full_text}")
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

# Обработка обычных текстовых сообщений
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
                
                # Отправляем сообщение при появлении первых символов
                if not sent_message:
                    sent_message = await message.answer(full_text)
                    last_updated_len = len(full_text)
                # Дописываем текст пачками (чтобы не превысить лимиты API Telegram)
                elif len(full_text) - last_updated_len > 20:
                    await sent_message.edit_text(full_text)
                    last_updated_len = len(full_text)

        # Финальное редактирование сообщения
        if sent_message and len(full_text) != last_updated_len:
            await sent_message.edit_text(full_text)

    except Exception as e:
        await message.answer(f"Ошибка при запросе к AI: {e}")

# Запуск бота
async def main():
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
