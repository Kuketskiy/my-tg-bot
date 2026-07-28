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
# Рабочее распознавание музыки через альтернативный Shazam API
# -------------------------------------------------------------------
async def recognize_music(file_path: str):
    wav_path = file_path + "_converted.wav"
    try:
        # Перекодируем файл в стандартный моно-формат для анализа
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", file_path,
            "-ar", "16000", "-ac", "1", wav_path,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        await proc.communicate()

        if not os.path.exists(wav_path):
            return None

        # Запрос к стороннему бесплатному обработчику Shazam
        async with aiohttp.ClientSession() as session:
            with open(wav_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field('file', f, filename='audio.wav', content_type='audio/wav')
                
                # Используем рабочий публичный прокси для Shazam
                async with session.post("https://api.vagalume.com.br/ajax/audio-recognize/", data=form) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        if res.get("status") == "success" and res.get("data"):
                            data = res["data"]
                            return {
                                "title": data.get("title", "Неизвестно"),
                                "subtitle": data.get("band", "Неизвестный исполнитель"),
                                "cover_url": data.get("cover")
                            }

        # Резервный метод через Shazam Web API
        async with aiohttp.ClientSession() as session:
            with open(wav_path, "rb") as f:
                audio_bytes = f.read()

            url = "https://amp.shazam.com/discovery/v5/ru/RU/android/-/tag/sample"
            headers = {"User-Agent": "Shazam/11.7.0 (Android; arm64-v8a)"}
            async with session.post(url, data=audio_bytes, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    track = data.get("track")
                    if track:
                        return {
                            "title": track.get("title"),
                            "subtitle": track.get("subtitle"),
                            "cover_url": track.get("images", {}).get("coverarthq")
                        }
        return None
    except Exception as e:
        print(f"Ошибка распознавания: {e}")
        return None
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

# -------------------------------------------------------------------
# Распознавание голоса
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
        print(f"Ошибка речи: {e}")
        return ""
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

# -------------------------------------------------------------------
# Хэндлеры
# -------------------------------------------------------------------
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("Привет! Я готов. Отправляй текст, голосовые или музыку!")

@dp.message(F.voice | F.audio | F.video_note)
async def handle_audio(message: Message):
    status_msg = await message.answer("🔎 Ищу трек...")
    
    file_id = message.voice.file_id if message.voice else (
        message.audio.file_id if message.audio else message.video_note.file_id
    )
    file = await bot.get_file(file_id)
    file_path = f"temp_{file_id}"
    await bot.download_file(file.file_path, file_path)

    try:
        music_info = await recognize_music(file_path)
        
        if music_info and music_info.get("title"):
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
                await bot.send_chat_action(message.chat.id, "typing")
                
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
                await status_msg.edit_text("Не удалось распознать музыкальный трек или речь.")
        else:
            await status_msg.edit_text("Музыка в файле не найдена.")

    except Exception as e:
        print(f"Ошибка при обработке: {e}")
        await status_msg.edit_text("Произошла ошибка при обработке файла.")

    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

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

async def main():
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
