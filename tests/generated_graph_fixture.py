def concrete_graph():
    question = {'question': 'What must be checked before work?', 'answers': ['Equipment condition', 'Nothing'], 'correct_answer': 'Equipment condition'}
    return {'nodes': [
        {'id': 'module-1', 'type': 'module', 'label': 'Module'},
        {'id': 'lesson-1', 'type': 'lesson', 'label': 'Lesson', 'content': 'Check equipment condition before work.'},
        {'id': 'test-module', 'type': 'test', 'assessment_scope': 'module', 'questions': [dict(question)]},
        {'id': 'test-final', 'type': 'test', 'assessment_scope': 'final', 'questions': [dict(question)]}],
        'edges': [{'source': 'module-1', 'target': 'lesson-1', 'relation': 'contains'},
                  {'source': 'module-1', 'target': 'test-module', 'relation': 'contains'}]}
