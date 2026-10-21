import pytest
from app.models.learning import AssessmentDefinition
from app.models.test import Test as Question
from app.models.task import Task
from app.services.course_materialization_service import CourseMaterializationService, _question_payload
from app.services.learning_service import build_publication_snapshot
from tests.factories import make_course


def test_generated_multiple_and_open_questions_keep_private_keys_and_rubric(db_session, auth_user):
    course = make_course(db_session, owner_id=auth_user.id)
    nodes = [{'id': 'm', 'type': 'module', 'label': 'Module'},
        {'id': 'l', 'type': 'lesson', 'label': 'Lesson', 'content': 'Published content',
         'cases': [{'id': 'case:1', 'title': 'Case', 'scenario': 'Visible scenario', 'prompts': ['Explain your choice'],
                    'expected_response': 'Private case answer', 'rubric': {'criteria': [{'id': 'accuracy', 'title': 'Accuracy', 'weight': 1,
                        'levels': [{'title': 'Incomplete', 'description': 'Missing evidence', 'score': 0}, {'title': 'Complete', 'description': 'Uses the relevant evidence', 'score': 2}]}]}}]},
        {'id': 't', 'type': 'test', 'assessment_scope': 'module', 'questions': [
            {'id': 'multi', 'kind': 'multiple_choice', 'question': 'Select both', 'answers': ['A', 'B', 'C'], 'correct_answers': ['A', 'C']},
            {'id': 'open', 'kind': 'short_answer', 'question': 'Explain', 'answers': [], 'expected_answer': 'Private expected answer'}]}]
    result = CourseMaterializationService.materialize(db_session, course=course, nodes=nodes,
        edges=[{'source': 'm', 'target': 'l', 'relation': 'contains'}, {'source': 'm', 'target': 't', 'relation': 'contains'}])
    db_session.flush()
    questions = db_session.query(Question).order_by(Question.id).all()
    definitions = [db_session.query(AssessmentDefinition).filter_by(test_id=q.id).one() for q in questions]
    assert definitions[0].question_type == 'multiple' and definitions[0].correct_answers == ['A', 'C']
    assert definitions[1].question_type == 'open' and definitions[1].grading_mode == 'manual'
    assert definitions[1].correct_answers == ['Private expected answer'] and definitions[1].criteria[0]['max_points'] == 1
    task = db_session.query(Task).one()
    assert 'Private case answer' not in task.description
    task_definition = db_session.query(AssessmentDefinition).filter_by(task_id=task.id).one()
    assert task_definition.correct_answers == ['Private case answer'] and task_definition.max_points == 2
    db_session.expire_all()
    snapshot = build_publication_snapshot(db_session, course, [m for m in course.modules if not m.is_deleted])
    assert snapshot['modules'][0]['assessments'][0]['correct_answers'] == ['A', 'C']
    assert snapshot['modules'][0]['assessments'][2]['criteria'][0]['max_points'] == 2
    assert result['test_question_count'] == 2


def test_empty_or_ambiguous_generated_assessment_is_not_materialized_as_placeholder():
    with pytest.raises(ValueError, match='concrete'):
        _question_payload({'id': 'test', 'label': 'Empty test'})
    with pytest.raises(ValueError, match='unambiguous'):
        _question_payload({'id': 'test', 'questions': [{'question': 'Select', 'kind': 'multiple_choice', 'answers': ['A', 'A'], 'correct_answers': ['A']}]})
