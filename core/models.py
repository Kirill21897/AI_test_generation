from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Any
from datetime import datetime
import uuid


class GenerationInput(BaseModel):
    """Основные праметры для генерации теста"""
    topic: str
    specialty: str
    level: Literal['Junior', 'Middle', 'Senior', "Expert"]
    num_questions: int = Field(5, ge=3, le=50)

    subdomain: Optional[str] = None
    additional_context: Optional[str] = None
    additional_topics: Optional[List[str]] = None

class Question(BaseModel):
    """Один вопрос в тесте"""
    id: str
    type: Literal['MCQ', 'Scenario', 'Calculation', 'Procedure']
    difficulty: Literal['Easy', 'Medium', 'Hard']
    bloom_level: Literal['Remember', 'Understand', 'Apply', 'Analyze']
    
    question_text: str
    options: Optional[List[str]] = None
    correct_answer: Any
    explanation: str
    distractor_explanations: Optional[List[str]] = None
    
    metadata: dict = Field(default_factory=dict)
    pass


class GeneratedTest(BaseModel):
    """Полный тест - результат работы LLM-core"""
    test_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    topic: str
    specialty: str
    level: str
    duration_minutes: int
    questions: List[Question]
    standards_covered: List[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict = Field(default_factory=dict)
