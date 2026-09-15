"""Export a live evaluation as a readable course with source page links."""
import argparse
import json
import os
import sys
from pathlib import Path
os.environ["DEBUG"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ai.evidence import learner_markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--report", default="report.json")
    parser.add_argument("--output-name", default="КУРС.md")
    parser.add_argument("--pdf-link", required=True, help="Relative URL from the exported file to the original PDF")
    args = parser.parse_args()
    out = args.directory
    report = json.loads((out / args.report).read_text())
    if not report.get("artifacts"):
        parser.error("No complete course in report.json; inspect agent-artifacts.json for partial results")
    artifacts = report["artifacts"]
    sources = {s["id"]: s for s in json.loads((out / "sources.json").read_text())}
    first, last = report["scope"]["pdf_pages"]
    verdict = {'pass':'проверка пройдена', 'revise':'нужны исправления', 'fail':'проверка не пройдена'}.get(artifacts['qa']['verdict'], 'проверка не завершена')
    lines = [f'# {report["scope"]["title"]}',
        f'Учебный прогон по страницам PDF {first}–{last}. Это часть книги; остальные страницы в курс не вошли.',
        'Статус автоматического контроля: **' + verdict + '**. '
        'Это черновик для проверки. Подробности — в [отчёте о качестве](ОТЧЁТ.md).',
        '## Содержание']
    for i, lesson in enumerate(artifacts["writer"]["lessons"], 1):
        lines.append(f'{i}. {lesson["title"]}')

    def citations(ids):
        return 'Источник: ' + ', '.join(
            f'[PDF, с. {page}]({args.pdf_link}#page={page})'
            for page in sorted({sources[s]["page"] for s in ids}))

    for i, lesson in enumerate(artifacts["writer"]["lessons"], 1):
        lines.extend([f'## Урок {i}. {lesson["title"]}', lesson["summary"]])
        for section in lesson["sections"]:
            lines.extend([f'### {section["heading"]}', learner_markdown(section["content_markdown"], set(section["source_ref_ids"])), citations(section["source_ref_ids"])])
        lines.extend(['### Главное', '\n'.join('- ' + text for text in lesson["key_takeaways"])])
    assessment = artifacts["assessment"]
    lines.append('## Вопросы для проверки')
    for i, q in enumerate(assessment["questions"], 1):
        lines.extend([f'### Вопрос {i}', q['prompt']])
        if q['options']:
            lines.append('\n'.join(f'{j}. {o["text"]}' for j, o in enumerate(q['options'], 1)))
        answer = q.get('expected_answer') or '; '.join(o['text'] for o in q['options'] if o['id'] in q['correct_option_ids'])
        lines.extend([f'**Ответ:** {answer}', f'**Объяснение:** {q["explanation"]}', citations(q['source_ref_ids'])])
    lines.append('## Практические задания')
    rubric_titles = {r['id']:r['title'] for r in assessment['rubrics']}
    for p in assessment['practices']:
        lines.extend([f'### {p["title"]}', p['instructions'], f'**Результат работы:** {p["deliverable"]}',
                      f'Критерии: {rubric_titles[p["rubric_id"]]}', citations(p['source_ref_ids'])])
    lines.append('## Кейсы')
    for case in assessment['cases']:
        lines.extend([f'### {case["title"]}', case['scenario'], '\n'.join('- ' + p for p in case['prompts']),
                      '**Разбор:** ' + case['expected_response'], citations(case['source_ref_ids'])])
    lines.append('## Критерии оценивания')
    for rubric in assessment['rubrics']:
        lines.extend([f'### {rubric["title"]}', f'Проходной балл: {rubric["passing_score"]}.'])
        for criterion in rubric['criteria']:
            lines.extend([f'**{criterion["title"]}** — вес {criterion["weight"]}. {criterion["description"]}',
                '\n'.join(f'- {level["score"]}: {level["description"]}' for level in criterion['levels'])])
    lines.extend(['## Статус проверки', 'Замечания к содержанию и работе рецензента собраны в [отчёте о качестве](ОТЧЁТ.md).'])
    lines.append('## Предупреждения при чтении источника')
    lines.extend('- ' + warning for warning in artifacts['ingestion']['warnings'])
    (out / args.output_name).write_text('\n\n'.join(lines) + '\n')
    print(out / args.output_name)


if __name__ == '__main__':
    main()
