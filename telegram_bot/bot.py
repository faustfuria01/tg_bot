import os
import json
import logging
import asyncio
import openai
import requests
import asyncpg

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)

from dotenv import load_dotenv

load_dotenv()

# Получение переменных окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BITRIX24_WEBHOOK = os.getenv("BITRIX24_WEBHOOK")
DATABASE_URL = os.getenv("DATABASE_URL")

openai.api_key = OPENAI_API_KEY

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Определяем состояния диалога
SEGMENT, QUESTIONNAIRE, AI_DIALOG = range(3)

# Список вопросов для анкеты
QUESTIONS = [
    "Как вас зовут?",
    "Какой у вас опыт в нашей сфере?",
    "Как вы узнали о нас?"
]

# функция для сохранения ответов в PostgreSQL
async def save_answer(user_id: int, question: str, answer: str):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        "INSERT INTO questionnaire(user_id, question, answer) VALUES($1, $2, $3)",
        user_id, question, answer
    )
    await conn.close()

# Обработчик команды /start
def start(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [InlineKeyboardButton("Компания", callback_data="segment_company"),
         InlineKeyboardButton("Частное лицо", callback_data="segment_individual")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Добро пожаловать! Пожалуйста, выберите категорию:", reply_markup=reply_markup)
    return SEGMENT

# Обработчик выбора сегмента
def segment_choice(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    segment = query.data.split("_")[1]
    context.user_data["segment"] = segment
    context.user_data["answers"] = []
    context.user_data["question_index"] = 0

    query.edit_message_text(text=f"Вы выбрали: {segment}. Давайте начнем анкетирование.")
    query.message.reply_text(QUESTIONS[0])
    return QUESTIONNAIRE

# Обработчик анкеты: сохраняем ответы и переходим к следующему вопросу или к диалогу с AI
def questionnaire(update: Update, context: CallbackContext) -> int:
    user_answer = update.message.text
    question_index = context.user_data.get("question_index", 0)
    current_question = QUESTIONS[question_index]

    # Сохраняем ответ в базе данных (асинхронно)
    try:
        # Если в текущем контексте уже есть запущенный event loop, создаем задачу
        loop = asyncio.get_running_loop()
        loop.create_task(save_answer(update.message.from_user.id, current_question, user_answer))
    except RuntimeError:
        # Если event loop не запущен, запускаем корутину и ждем её завершения
        asyncio.run(save_answer(update.message.from_user.id, current_question, user_answer))

    context.user_data.setdefault("answers", []).append({current_question: user_answer})
    question_index += 1
    context.user_data["question_index"] = question_index

    if question_index < len(QUESTIONS):
        update.message.reply_text(QUESTIONS[question_index])
        return QUESTIONNAIRE
    else:
        update.message.reply_text("Спасибо за ответы! Начинаем диалог с AI. Задайте ваш вопрос.")
        # Создание или обновление сделки в Bitrix24
        user_data = {
            "name": context.user_data["answers"][0].get(QUESTIONS[0]),
            "segment": context.user_data.get("segment"),
            "answers": context.user_data.get("answers")
        }
        try:
            payload = {
                "fields": {
                    "TITLE": f"Сделка от пользователя {user_data.get('name', '')}",
                    "COMMENTS": json.dumps(user_data, ensure_ascii=False)
                }
            }
            response = requests.post(BITRIX24_WEBHOOK, json=payload)
            logger.info("Bitrix24 response: %s", response.text)
        except Exception as e:
            logger.error("Error updating Bitrix24: %s", e)
        return AI_DIALOG

# Обработчик диалога с AI
def ai_dialog(update: Update, context: CallbackContext) -> int:
    user_query = update.message.text
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": user_query}],
            temperature=0.7,
            max_tokens=150
        )
        ai_answer = response.choices[0].message["content"].strip()
    except Exception as e:
        logger.error("OpenAI error: %s", e)
        ai_answer = "Произошла ошибка при обращении к AI."

    update.message.reply_text(ai_answer)

    # Добавляем inline-кнопки для дальнейших действий
    keyboard = [
        [InlineKeyboardButton("Связаться с менеджером", callback_data="contact_manager")],
        [InlineKeyboardButton("Перейти к оплате", callback_data="proceed_payment")],
        [InlineKeyboardButton("Дополнительный вопрос", callback_data="additional_question")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Выберите действие:", reply_markup=reply_markup)
    return AI_DIALOG

# Обработчик нажатий inline-кнопок
def inline_buttons(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    action = query.data
    if action == "contact_manager":
        text = "Менеджер свяжется с вами в ближайшее время."
    elif action == "proceed_payment":
        text = "Переход к оплате: [ссылка](https://your-payment-link.example.com)"
    elif action == "additional_question":
        text = "Пожалуйста, задайте дополнительный вопрос."
    else:
        text = "Действие не распознано."
    query.edit_message_text(text=text)
    return AI_DIALOG

def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("Диалог завершен.")
    return ConversationHandler.END

def main():
    updater = Updater(TELEGRAM_BOT_TOKEN)
    dispatcher = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SEGMENT: [CallbackQueryHandler(segment_choice, pattern='^segment_')],
            QUESTIONNAIRE: [MessageHandler(Filters.text & ~Filters.command, questionnaire)],
            AI_DIALOG: [MessageHandler(Filters.text & ~Filters.command, ai_dialog),
                        CallbackQueryHandler(inline_buttons, pattern='^(contact_manager|proceed_payment|additional_question)$')]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    dispatcher.add_handler(conv_handler)
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
