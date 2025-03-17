require('dotenv').config();
const express = require('express');
const http = require('http');
const axios = require('axios');
const { Server } = require('socket.io');
const path = require('path');

const app = express();
const server = http.createServer(app);
const io = new Server(server);

const PORT = process.env.PORT || 3000;
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
const BITRIX24_WEBHOOK = process.env.BITRIX24_WEBHOOK;

// Список вопросов для динамической анкеты
const QUESTIONS = [
  "Как вас зовут?",
  "Какой у вас опыт в нашей сфере?",
  "Как вы узнали о нас?"
];

// Раздача статических файлов из папки public
app.use(express.static(path.join(__dirname, 'public')));

// Эндпоинт для проверки работы сервера
app.get('/health', (req, res) => {
  res.send('Server is running.');
});

// Функция для вызова GPT-4 через OpenAI API
async function askGPT4(prompt) {
  try {
    const response = await axios.post(
      'https://api.openai.com/v1/chat/completions',
      {
        model: "gpt-4", // Если нет доступа к GPT-4, замените на "gpt-3.5-turbo"
        messages: [{ role: "user", content: prompt }],
        temperature: 0.7,
        max_tokens: 150
      },
      {
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${OPENAI_API_KEY}`
        }
      }
    );
    return response.data.choices[0].message.content.trim();
  } catch (error) {
    console.error("OpenAI API error:", error.response ? error.response.data : error.message);
    return "Произошла ошибка при обращении к AI.";
  }
}

// Функция для интеграции с Bitrix24
async function updateBitrix24Deal(userData) {
  try {
    const payload = {
      fields: {
        TITLE: `Сделка от пользователя ${userData.name || ''}`,
        COMMENTS: JSON.stringify(userData)
      }
    };
    const response = await axios.post(BITRIX24_WEBHOOK, payload);
    return response.data;
  } catch (error) {
    console.error("Bitrix24 API error:", error.response ? error.response.data : error.message);
    return null;
  }
}

// События Socket.io для обработки чата
io.on('connection', (socket) => {
  console.log('Новый пользователь подключился');

  // Инициализируем состояние сокета
  socket.data.mode = "idle"; // Режим: idle, questionnaire, ai_dialog
  socket.data.questionIndex = 0;
  socket.data.answers = [];
  socket.data.userData = {};

  // Событие получения контактных данных и начала сессии
  socket.on('start_chat', async (data) => {
    // data: { name, email, phone }
    socket.data.userData = data;
    // Обновляем сделку в Bitrix24 с контактными данными
    await updateBitrix24Deal(data);
    // Отправляем приветственное сообщение с инструкцией
    socket.emit('message', { from: 'bot', text: 'Добро пожаловать! Для прохождения анкетирования введите "анкета", или задайте ваш вопрос для диалога с AI.' });
  });

  // Событие получения сообщений от клиента
  socket.on('message', async (msg) => {
    console.log('Получено сообщение:', msg);

    // Если пользователь находится в режиме анкетирования, обрабатываем ответ
    if (socket.data.mode === "questionnaire") {
      const idx = socket.data.questionIndex;
      // Сохраняем ответ на текущий вопрос
      socket.data.answers.push({ question: QUESTIONS[idx], answer: msg });
      socket.data.questionIndex++;

      // Если есть следующий вопрос, отправляем его
      if (socket.data.questionIndex < QUESTIONS.length) {
        const nextQuestion = QUESTIONS[socket.data.questionIndex];
        socket.emit('message', { from: 'bot', text: nextQuestion });
      } else {
        // Анкета завершена: переключаем режим на AI-диалог
        socket.data.mode = "ai_dialog";
        socket.emit('message', { from: 'bot', text: "Анкета завершена. Спасибо за ответы! Теперь задайте ваш вопрос." });
        // Обновляем сделку в Bitrix24 с данными анкеты
        await updateBitrix24Deal({
          name: socket.data.userData.name,
          questionnaire: socket.data.answers
        });
      }
      return;
    }

    // Если пользователь отправляет команду для начала анкеты
    if (msg.toLowerCase().trim() === "анкета") {
      socket.data.mode = "questionnaire";
      socket.data.questionIndex = 0;
      socket.data.answers = [];
      socket.emit('message', { from: 'bot', text: "Начинаем анкетирование. " + QUESTIONS[0] });
      return;
    }

    // По умолчанию, если не в режиме анкеты – обрабатываем сообщение как запрос к AI
    const aiResponse = await askGPT4(msg);
    socket.emit('message', { from: 'bot', text: aiResponse });
  });

  // Событие подключения менеджера
  socket.on('connect_manager', () => {

    socket.emit('message', { from: 'bot', text: 'Менеджер скоро подключится.' });
  });
});

server.listen(PORT, () => {
  console.log(`Server listening on port ${PORT}`);
});
