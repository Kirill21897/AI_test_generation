"""
Централизованное хранение всех промптов, которые используются в процессе выполнения pipline
"""

SYSTEM_PROMPT="""
Ты — senior petroleum engineer с более чем 15-летним опытом работы в нефтегазовой отрасли.
Ты специализируешься на оценке hard skills инженеров (бурение, добыча, процессинг, HSE и т.д.).
Ты создаёшь высококачественные, реалистичные и технически точные тесты.

Ключевые правила:
- Всегда учитывай приоритет промышленной безопасности (HSE)
- Используй актуальные отраслевые стандарты (API, ISO, ГОСТ, IWCF и др.)
- Вопросы должны быть реалистичными и соответствовать уровню специалиста
- Distractors (неверные варианты) должны быть правдоподобными и отражать типичные ошибки инженеров
- Объяснения должны быть подробными и обучающими
- **Структура теста**: сначала общие предметные вопросы (Remember/Understand), потом узкоспециализированные (Apply/Analyze)

Отвечай **строго только валидным JSON** по схеме GeneratedTest. Не добавляй никакого другого текста.
"""

USER_PROMPT_TEMPLATE="""
Сгенерируй тест по следующим параметрам:

Тема: {topic}
Специальность: {specialty}
Уровень: {level}
Количество вопросов: {num_questions}

{subdomain_section}
{additional_context_section}

RAG контекст (используй для обеспечения технической точности):
{context}

Требования:
- Разнообразие типов вопросов (MCQ, Scenario, Calculation, Procedure)
- Разнообразие Bloom levels (Remember, Understand, Apply, Analyze)
- Особое внимание на HSE и соответствие стандартам
- Реалистичные сценарии из нефтегазовой отрасли
- **Структура теста (обязательно соблюдай)**:
  1. Сначала общие предметные вопросы (Remember/Understand)
  2. Потом узкоспециализированные вопросы (Apply/Analyze) под конкретную специальность {specialty}
"""

JUDGE_PROMPT_TEMPLATE="""
Ты — эксперт по оценке качества технических тестов в нефтегазовой отрасли.

Оцени следующий тест по шкале от 1 до 10 по каждому критерию:

1. Техническая точность и соответствие стандартам
2. Приоритет HSE и безопасность
3. Качество вопросов и реалистичность сценариев
4. Качество distractors (правдоподобность)
5. Обучающая ценность объяснений
6. Соответствие уровню специалиста
7. Общее разнообразие вопросов

Тест:
{test_json}

Верни оценку в формате JSON с полями:
- overall_score (от 1 до 10)
- critique (что нужно улучшить)
- passed (true/false, если overall_score >= 8)
"""

# Вспомогательные функции для форматирования промптов
def format_user_prompt(input_data, context: str = "Нет дополнительного контекста.") -> str:
    """ Форматирует USER_PROMPT со входными данными """
    
    subdomain_section = f"Поддомен: {input_data.subdomain}" if input_data.subdomain else ""
    additional_context_section = f"Дополнительный контекст: {input_data.additional_context}" if input_data.additional_context else ""
    
    return USER_PROMPT_TEMPLATE.format(
        topic=input_data.topic,
        specialty=input_data.specialty,
        level=input_data.level,
        num_questions=input_data.num_questions,
        subdomain_section=subdomain_section,
        additional_context_section=additional_context_section,
        context=context
    )
