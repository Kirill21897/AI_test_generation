"""
Централизованное хранение всех промптов, которые используются в процессе выполнения pipline
"""

SYSTEM_PROMPT="""
Ты — senior petroleum engineer в нефтегазовой отрасли, создаёшь технические тесты.
Отвечай **ТОЛЬКО валидным JSON**, без дополнительного текста.
"""

USER_PROMPT_TEMPLATE="""
Сгенерируй технический тест СТРОГО на {num_questions} вопросов.

Тема: {topic}
Специальность: {specialty}
Уровень: {level}

КРИТИЧЕСКИЕ ПРАВИЛА - СЛЕДУЙ ТОЧНО:
1. Ответь ТОЛЬКО валидным JSON-объектом, без другого текста
2. Каждый вопрос должен иметь:
   - "id": число (1, 2, 3...)
   - "type": один из ["MCQ", "Scenario", "Calculation", "Procedure"]
   - "difficulty": один из ["Easy", "Medium", "Hard"]
   - "bloom_level": один из ["Remember", "Understand", "Apply", "Analyze"]
   - "question_text": текст вопроса
   - "options": массив строк (для MCQ) или null
   - "correct_answer": правильный ответ
   - "explanation": подробное объяснение
3. Все вопросы и ответы - на РУССКОМ языке
4. Обязательно учитывай HSE и отраслевые стандарты
{additional_topics_section}

Верни JSON в ТОЧНО этом формате:
{{
  "title": "Название теста",
  "topic": "{topic}",
  "specialty": "{specialty}",
  "level": "{level}",
  "duration_minutes": 30,
  "questions": [
    {{
      "id": 1,
      "type": "MCQ",
      "difficulty": "Medium",
      "bloom_level": "Apply",
      "question_text": "Текст вопроса здесь?",
      "options": ["Вариант A", "Вариант B", "Вариант C", "Вариант D"],
      "correct_answer": "Вариант B",
      "explanation": "Почему это правильно..."
    }}
  ],
  "standards_covered": ["API 53", "IWCF"]
}}
"""

JUDGE_PROMPT_TEMPLATE="""
Оцени качество этого технического теста. Ответь ТОЛЬКО валидным JSON.

Тест:
{test_json}

Верни JSON в ТОЧНО этом формате:
{{
  "overall_score": 8.5,
  "critique": "Что можно улучшить...",
  "passed": true
}}
"""

# Вспомогательные функции для форматирования промптов
def format_user_prompt(input_data, context: str = "Нет дополнительного контекста.") -> str:
    """ Форматирует USER_PROMPT со входными данными """
    
    subdomain_section = f"Поддомен: {input_data.subdomain}" if input_data.subdomain else ""
    additional_context_section = f"Дополнительный контекст: {input_data.additional_context}" if input_data.additional_context else ""
    
    if input_data.additional_topics:
        topics_list = ", ".join(input_data.additional_topics)
        additional_topics_section = f"\n5. В тесте ОБЯЗАТЕЛЬНО должны присутствовать вопросы по темам: {topics_list}. Однако тест не должен ограничиваться только этими темами, включай и другие вопросы по основной теме."
    else:
        additional_topics_section = ""
    
    return USER_PROMPT_TEMPLATE.format(
        topic=input_data.topic,
        specialty=input_data.specialty,
        level=input_data.level,
        num_questions=input_data.num_questions,
        additional_topics_section=additional_topics_section
    )
