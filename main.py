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
# Функция распознавания музыки через прямой веб-запрос (без shazamio_core)
# -------------------------------------------------------------------
async def recognize_music(file_path: str):
    raw_path = file_path + ".raw"
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", file_path,
            "-f", "s16le", "-ac", "1", "-ar", "44100", raw_path,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        await proc.communicate()

        if not os.path.exists(raw_path):
            return None

        async with aiohttp.ClientSession() as session:
            with open(raw_path, "rb") as f:
                audio_bytes = f.read()

            url = "https://amp.shazam.com/discovery/v5/ru/RU/android/-/tag/sample"
            headers = {"Content-Type": "application/octet-stream"}
            
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
        print(f"Ошибка распознавания музыки: {e}")
        return None
    finally:
        if os.path.exists(raw_path):
            os.remove(raw_path)

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
        music_info = await recognize_music(file_path)
        
        if music_info:
            res_text = f"🎵 **Найден трек!**\n\n📌 **Название:** {music_info['title']}\n👤 **Исполнитель:** {music_info['subtitle']}"
            if music_info.get("cover_url"):
                await message.answer_photo(photo=music_info["cover_url"], caption=res_text, parse_mode="Markdown")
            else:
                await message.answer(res_text, parse_mode="Markdown")
            await status_msg.delete()
            return

        if message.voice or message.video_note:
            text = await transcribe_voice(file_path)
            if text:
                await status_msg.edit_text(f"🗣 **Вы сказали:** _{text}_\n\nДумаю над ответом...", parse_mode="Markdown")
                
                # Включаем статус печати перед запросом к нейросети
                await bot.send_chat_action(message.chat.id, "typing")
                
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
        # Включаем статус "печатает..." сразу, как пришло сообщение
        await bot.send_chat_action(message.chat.id, "typing")
        
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
