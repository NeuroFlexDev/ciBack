# 🧠 NeuroLearn — Backend (ciBack)

Бэкенд-приложение на FastAPI для генерации и управления образовательными онлайн-курсами с помощью HuggingChat (LLM) и RAG-подходов.

---

## 🚀 Возможности

- ✅ CRUD для **курсов**, **модулей**, **уроков**, **тестов**, **заданий**, **теории**
- 🤖 Интеграция с HuggingChat для генерации:
  - Структуры курса
  - Модулей и уроков
  - Теории, тестов, заданий
- 🧠 Поддержка RAG: анализ и суммаризация текстов/файлов
- 📄 Загрузка PDF, DOCX и TXT файлов
- 🧰 Полный OpenAPI (Swagger UI) с примерами
- 🔧 Поддержка структуры курса (`course_structure`) и авто-генерация по шаблонам Jinja2

---

## 📦 Установка

```bash
# Клонируем репозиторий
git clone https://github.com/your-org/NeuroLearn.git
cd NeuroLearn/ciBack

# Создаём виртуальное окружение
python -m venv venv
source venv/bin/activate

# Устанавливаем зависимости
pip install -r requirements.txt
```

---

## ⚙️ Переменные окружения

В корне проекта создайте файл `.env` или настройте переменные окружения:

```env
HF_EMAIL=youremail@example.com
HF_PASS=yourpassword
```

> Для авторизации в HuggingFace через `hugchat`.

---

## 🧪 Запуск приложения

```bash
uvicorn main:app --reload
```

После запуска:

- Документация: http://localhost:8000/docs
- OpenAPI JSON: http://localhost:8000/openapi.json

---

## 📁 Структура проекта

```
ciBack/
├── app/
│   ├── routes/               # Все API-роутеры
│   ├── models/               # SQLAlchemy модели
│   ├── database/             # DB подключение
│   ├── services/             # LLM / HuggingFace / генерация
│   └── prompts/              # Jinja2 шаблоны
├── main.py                   # Точка входа
└── requirements.txt
```

---

## 📘 Примеры API

### 🔸 Генерация модулей
```http
GET /api/courses/{course_id}/generate_modules
```

### 🔸 Загрузка документа и обновление описания курса (RAG)
```http
POST /api/courses/{course_id}/upload-description
Content-Type: multipart/form-data
```

---

# 🗺️ Roadmap — NeuroLearn Backend (ciBack)

> Актуальное состояние разработки серверной части платформы генерации курсов.

---

## ✅ Готово

- [x] CRUD API:
  - Курсы
  - Модули
  - Уроки
  - Теория
  - Тесты
  - Задания
- [x] Генерация структуры курсов с помощью HuggingChat
- [x] Генерация теории, тестов, заданий по шаблонам Jinja2
- [x] Semantic Search: SentenceTransformers + FAISS
- [x] Загрузка и анализ PDF/DOCX/TXT-файлов (RAG-подход)
- [x] Версионирование: CourseVersion / ModuleVersion / LessonVersion
- [x] Обратная связь (Feedback) с HITL-режимом
- [x] Экспорт курсов в Markdown и PDF
- [x] OpenAPI + Swagger UI со всеми эндпоинтами

---

## 🛠️ В разработке

- [ ] RAG-интеграция с arXiv, Semantic Scholar, Crossref
- [ ] Langchain/LLM-агент для диалога с курсом
- [ ] Рефакторинг генератора курсов через `course_agent.py`
- [ ] CI/CD (GitHub Actions)

---

## 🔮 В планах

- [ ] Поддержка SCORM/xAPI импорта и экспорта
- [ ] Расширенные роли и ACL (ученик / преподаватель / редактор)
- [ ] Система истории и отката изменений
- [ ] Облачный режим + локальный сервер
- [ ] Поддержка WebSocket-нотификаций

---

_Последнее обновление: 2025-04-11_

---

## 🧠 Made with ❤️ by NeuroFlex  
Миссия: *"Показать всем буйство нейронов"*