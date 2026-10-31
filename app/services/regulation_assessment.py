"""Small business instructions use a bounded, explicit assessment contract.

The mode is frozen when a generation starts; legacy jobs keep their own contract.
"""

def regulation_assessment_mode(course, settings):
    if not course.position_id or settings.lesson_count != 1:
        return None
    return 'test_only' if settings.difficulty in {'basic', 'internship'} else 'test_and_practice'


def validate_regulation_assessment(value, mode):
    if not mode:
        if not value.practices or not value.cases or not value.rubrics:
            raise ValueError('Assessment must include practices, cases and rubrics')
        return
    if not 5 <= len(value.questions) <= 15:
        raise ValueError('A regulation needs 5–15 focused test questions covering its key procedures')
    if any(q.kind not in {'single_choice', 'multiple_choice'} for q in value.questions):
        raise ValueError('Regulation test questions must have selectable answers and automatic grading')
    if mode == 'test_only':
        if value.practices or value.cases or value.rubrics:
            raise ValueError('Basic regulation: tests only; practices, cases and rubrics must be empty')
    elif mode == 'test_and_practice':
        if len(value.practices) + len(value.cases) != 2 or not value.rubrics:
            raise ValueError('Advanced regulation: exactly two practical scenarios with grading rubrics')
    else:
        raise ValueError('Unknown regulation assessment contract')


def practice_unlocked(snapshot, responses, pass_percent):
    if not snapshot.get('practice_requires_test_pass'):
        return True
    questions = [a for m in snapshot.get('modules', []) for a in m.get('assessments', [])] + snapshot.get('assessments', [])
    tests = [a for a in questions if a['question_type'] != 'open']
    latest = {}
    for row in responses:
        old = latest.get(row.assessment_id)
        if old is None or (row.attempt, row.revision) > (old.attempt, old.revision):
            latest[row.assessment_id] = row
    return bool(tests) and all(
        (row := latest.get(test['id'])) is not None and row.status == 'reviewed'
        and row.points is not None and row.points >= test['max_points'] * pass_percent / 100
        for test in tests
    )
