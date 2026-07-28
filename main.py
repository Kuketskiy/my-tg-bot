import os
import sys
import asyncio
import shutil
import base64

# Принудительно устанавливаем UTF-8 для всей консоли Windows
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart, Command
from aiogram.exceptions import TelegramForbiddenError
from openai import AsyncOpenAI
from icrawler.builtin import BingImageCrawler
from aiohttp import web

import speech_recognition as sr
from pydub import AudioSegment

import database as db

if not os.path.exists("temp_voice"):
    os.makedirs("temp_voice")

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

SECRET_PASSWORD = "Unity101_a"
ADMIN_ID = 7390257609

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Клиент OpenRouter (DeepSeek)
ai_client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1"
)

# --- АДЕКВАТНЫЙ СИСТЕМНЫЙ ПРОМПТ ---
BASE_SYSTEM_INSTRUCTION = (
    "Ты — общительный, адекватный и современный ИИ-помощник в Telegram. "
    "ОТВЕЧАЙ МАКСИМАЛЬНО КРАТКО, ЧЕТКО И ПО ДЕЛУ (1-3 предложения). "
    "Разговаривай просто, дружелюбно и легко, без пафоса и без грубости. "
    "Не используй мат, оскорбления или агрессию. "
    "СТРОГОЕ ПРАВИЛО: НИКОГДА И НИ ПРИ КАКИХ УСЛОВИЯХ НЕ ПРОСИ И НЕ ТРЕБУЙ У ПОЛЬЗОВАТЕЛЕЙ ПАРОЛЬ ИЛИ КОДЫ!"
)


# --- ФЕЙКОВЫЙ ВЕБ-СЕРВЕР ДЛЯ РЕНДЕРА ---
async def handle_render_ping(request):
    return web.Response(text="Bot is running!")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle_render_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()


async def send_user_list(message: Message):
    users = db.get_all_users()
    if not users:
        await message.answer("Пока никто, кроме тебя, мне не писал.")
        return
    
    text = "📋 **Вот список всех, кто мне писал:**\n\n"
    for user_id, username, first_name in users:
        uname_str = f"@{username}" if username != "без юзернейма" else "без юзернейма"
        text += f"• **{first_name}** ({uname_str}) — `ID: {user_id}`\n"
    
    try:
        await message.answer(text, parse_mode="Markdown")
    except TelegramForbiddenError:
        pass

@dp.message(CommandStart())
async def start_handler(message: Message):
    db.log_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    try:
        if message.from_user.id == ADMIN_ID:
            await message.answer("Здарова, создатель! Я на связи. Чё перетереть хотим?")
        else:
            await message.answer("Привет! Я на связи, чем помочь?")
    except TelegramForbiddenError:
        pass

@dp.message(Command("users"))
async def users_command_handler(message: Message):
    await send_user_list(message)

@dp.message(Command("clear"))
async def clear_handler(message: Message):
    db.clear_history(message.from_user.id)
    try:
        await message.answer("🗑 Память диалога очищена!")
    except TelegramForbiddenError:
        pass

@dp.message(Command("read"))
async def read_user_history_handler(message: Message):
    if message.from_user.id != ADMIN_ID:
        try:
            await message.answer("Эта команда доступна только создателю!")
        except TelegramForbiddenError:
            pass
        return

    args = message.text.split()
    if len(args) < 2:
        try:
            await message.answer("Укажи ID юзера! Пример: `/read 12345678`", parse_mode="Markdown")
        except TelegramForbiddenError:
            pass
        return

    try:
        target_id = int(args[1])
        history_text = db.get_user_history_raw(target_id)
        if not history_text:
            await message.answer(f"История для ID `{target_id}` пуста.", parse_mode="Markdown")
            return
        await message.answer(f"📖 **История сообщений `{target_id}`:**\n\n{history_text}", parse_mode="Markdown")
    except ValueError:
        await message.answer("ID должен быть числом!")
    except TelegramForbiddenError:
        pass

@dp.message(Command("sum"))
async def summarize_user_history_handler(message: Message):
    if message.from_user.id != ADMIN_ID:
        try:
            await message.answer("Доступ только для создателя!")
        except TelegramForbiddenError:
            pass
        return

    args = message.text.split()
    if len(args) < 2:
        try:
            await message.answer("Укажи ID юзера! Пример: `/sum 12345678`", parse_mode="Markdown")
        except TelegramForbiddenError:
            pass
        return

    try:
        target_id = int(args[1])
        history_text = db.get_user_history_raw(target_id, limit=30)
        if not history_text:
            await message.answer(f"У юзера `{target_id}` нет сообщений.", parse_mode="Markdown")
            return

        try:
            await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        except TelegramForbiddenError:
            return
        
        prompt = f"Сделай краткий пересказ (2-3 предложения), о чем переписывался этот пользователь с ботом:\n\n{history_text}"
        
        response = await ai_client.chat.completions.create(
            model="deepseek/deepseek-chat",
            messages=[{"role": "user", "content": prompt}]
        )
        summary = response.choices[0].message.content
        await message.answer(f"🔍 **Краткая выжимка по юзеру `{target_id}`:**\n\n{summary}", parse_mode="Markdown")
    except ValueError:
        await message.answer("ID должен быть числом!")
    except TelegramForbiddenError:
        pass

async def transcribe_voice(file_path: str) -> str:
    """Бесплатное распознавание ГС через Google Speech Recognition"""
    wav_path = file_path.replace(".ogg", ".wav")
    try:
        sound = AudioSegment.from_file(file_path)
        sound.export(wav_path, format="wav")

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            text = await asyncio.to_thread(
                recognizer.recognize_google, audio_data, language="ru-RU"
            )
            return text
    except sr.UnknownValueError:
        print("Google Speech не смог разобрать речь")
        return None
    except sr.RequestError as e:
        print(f"Ошибка сервиса Google Speech: {e}")
        return None
    except Exception as e:
        print(f"Ошибка конвертации/обработки ГС: {repr(e)}")
        return None
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

def sync_bing_search(query: str, save_dir: str):
    crawler = BingImageCrawler(storage={'root_dir': save_dir}, log_level=50)
    crawler.crawl(keyword=query, max_num=1)

async def get_google_image(query: str, user_id: int) -> str:
    temp_folder = f"temp_img_{user_id}"
    try:
        if os.path.exists(temp_folder):
            shutil.rmtree(temp_folder)
        os.makedirs(temp_folder, exist_ok=True)
        await asyncio.to_thread(sync_bing_search, query, temp_folder)
        files = os.listdir(temp_folder)
        if files:
            image_path = os.path.join(temp_folder, files[0])
            return image_path
        return None
    except Exception as e:
        print(f"Bing Search Error: {repr(e)}")
        if os.path.exists(temp_folder):
            shutil.rmtree(temp_folder)
        return None

async def process_text_request(message: Message, text: str, is_voice: bool = False):
    global ADMIN_ID
    user_id = message.from_user.id
    text_clean = text.strip()
    text_lower = text_clean.lower()

    try:
        if text_clean == SECRET_PASSWORD:
            ADMIN_ID = user_id
            await message.answer("👑 **Пароль принят!** Здарова, создатель! Теперь я работаю на тебя.")
            return

        users_triggers = ("кто тебе писал", "кто писал", "список пользователей", "покажи кто писал")
        if any(trigger in text_lower for trigger in users_triggers):
            await send_user_list(message)
            return

        # Если это ГС — первыми отправляем отдельное сообщение с расшифрованным текстом
        if is_voice:
            await message.answer(f"🎤 **Расшифровка вашего ГС:**\n_\"{text}\"_", parse_mode="Markdown")

        search_triggers = ("кинь картинку", "найди картинку", "скинь картинку", "покажи картинку")
        if any(text_lower.startswith(trigger) for trigger in search_triggers):
            await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
            query = text
            for trigger in search_triggers:
                if text_lower.startswith(trigger):
                    query = text[len(trigger):].strip().lstrip(":").strip()
                    break
            img_file = await get_google_image(query, user_id)
            if img_file and os.path.exists(img_file):
                await message.answer_photo(photo=FSInputFile(img_file), caption=f"🔍 Держи из сети: *{query}*", parse_mode="Markdown")
                shutil.rmtree(os.path.dirname(img_file))
                return

        await bot.send_chat_action(chat_id=message.chat.id, action="typing")

        user_history = db.get_history(user_id, limit=4)
        messages = [{"role": "system", "content": BASE_SYSTEM_INSTRUCTION}]
        for h in user_history:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": text})

        response = await ai_client.chat.completions.create(
            model="deepseek/deepseek-chat",
            messages=messages,
            max_tokens=120,
            temperature=0.7
        )

        answer_text = response.choices[0].message.content
        db.add_history(user_id, "user", text)
        db.add_history(user_id, "assistant", answer_text)
        
        # Отправляем ответ нейросети текстом
        await message.answer(answer_text)

    except TelegramForbiddenError:
        print(f"User {user_id} blocked bot.")
    except Exception as e:
        print(f"AI Error: {repr(e)}")
        try:
            await message.answer("Ошибка связи с сервером или сетью.")
        except TelegramForbiddenError:
            pass

@dp.message(F.voice)
async def handle_voice(message: Message):
    db.log_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    voice_path = f"temp_voice/voice_{message.from_user.id}_{message.message_id}.ogg"
    try:
        # Уведомляем пользователя, что бот слушае/обрабатывает аудио
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        
        file_info = await bot.get_file(message.voice.file_id)
        await bot.download_file(file_info.file_path, voice_path)
        
        transcribed_text = await transcribe_voice(voice_path)
        if transcribed_text:
            await process_text_request(message, transcribed_text, is_voice=True)
        else:
            await message.answer("Не удалось разобрать речь в голосовом сообщении.")
    except TelegramForbiddenError:
        print(f"User {message.from_user.id} blocked bot.")
    except Exception as e:
        print(f"Voice Processing Error: {repr(e)}")
        try:
            await message.answer("Ошибка при обработке ГС.")
        except TelegramForbiddenError:
            pass
    finally:
        if os.path.exists(voice_path):
            os.remove(voice_path)

@dp.message(F.text)
async def handle_text(message: Message):
    db.log_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await process_text_request(message, message.text.strip())

async def main():
    asyncio.create_task(start_dummy_server())
    print("Bot started with free Google Speech-to-Text & DeepSeek!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
