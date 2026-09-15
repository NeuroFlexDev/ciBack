"""Live course-grounded chat probe with two cumulative memory compactions."""
import argparse
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    args = parser.parse_args()
    out = args.directory.resolve()
    if not (out / "manifest.json").exists() or not (out / "evaluation.sqlite").exists():
        parser.error("Directory must contain an isolated material evaluation")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.update(DATABASE_URL="sqlite:///" + str(out / "evaluation.sqlite"), ENV="evaluation",
        DEBUG="false", CHAT_HISTORY_MESSAGES="2", VSELLM_API_KEY=args.credentials.read_text().strip())
    import app.models  # noqa: F401
    from app.database.db import SessionLocal
    from app.models.chat import Chat
    from app.models.course import Course
    from app.models.ai_state import ChatMemory
    from app.services.chat_service import chat_generate
    session = SessionLocal()
    course = session.query(Course).one()
    chat = Chat(owner_id=course.owner_id, course_id=course.id, title="Проверка накопительной памяти", engine="vsellm")
    session.add(chat); session.commit()
    report = dict(success=False, messages=[], history_limit_for_test=2)
    started = time.monotonic()
    try:
        questions = [
            "Я изучаю тему впервые. Запомни мой пример: A={1,2}. Коротко объясни разницу между элементом и подмножеством на этом примере; ссылайся на материалы.",
            "А пустое множество является элементом моего A или его подмножеством? Ответь коротко и объясни разницу.",
            "Какой именно набор A я задал в начале и что сообщил о своём уровне подготовки? Восстанови сведения из переписки, не заменяй пример другим.",
            "Ещё раз коротко: какой мой исходный пример и что именно я сказал о своём уровне? Укажи, откуда тебе это известно.",
        ]
        for question in questions:
            print("chat request", len(report["messages"]) + 1, flush=True)
            answer = chat_generate(chat_id=chat.id, user_id=course.owner_id, text=question, db=session, engine_name="vsellm")
            memory = session.get(ChatMemory, chat.id)
            report["messages"].append(dict(user=question, assistant=answer["answer"], usage=answer["raw"].get("usage"),
                model=answer["raw"].get("model"), memory=dict(summary=memory.summary, through_message_id=memory.through_message_id) if memory else None))
        report["success"] = True
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    finally:
        report["seconds"] = round(time.monotonic() - started, 2)
        path = out / "chat-cumulative.json"
        if path.exists():
            path.rename(out / f"chat-cumulative-{time.time_ns()}.json")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        session.close()
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
