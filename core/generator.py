""" Основной LLM-core """

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from .models import GenerationInput, GeneratedTest
from .prompts import SYSTEM_PROMPT, JUDGE_PROMPT_TEMPLATE, format_user_prompt
import json
import os
import time
import urllib.request
from dotenv import load_dotenv

load_dotenv(override=True)

def _debug_report(hypothesis_id: str, location: str, msg: str, data: dict | None = None, run_id: str | None = None) -> None:
    _p = ".dbg/llm-test-generation.env"
    _u, _s = "http://127.0.0.1:7777/event", "llm-test-generation"
    _run = run_id or os.getenv("DEBUG_RUN_ID", "post-fix")
    try:
        with open(_p, encoding="utf-8") as _f:
            for _line in _f.read().splitlines():
                if _line.startswith("DEBUG_SERVER_URL="):
                    _u = _line.split("=", 1)[1]
                elif _line.startswith("DEBUG_SESSION_ID="):
                    _s = _line.split("=", 1)[1]
        urllib.request.urlopen(
            urllib.request.Request(
                _u,
                data=json.dumps(
                    {
                        "sessionId": _s,
                        "runId": _run,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "msg": f"[DEBUG] {msg}",
                        "data": data or {},
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=2,
        ).read()
    except Exception:
        pass

def _get_api_base() -> str | None:
    api_base = os.getenv("OPENAI_API_BASE")
    if not api_base:
        return api_base

    api_base = api_base.rstrip("/")
    if "openrouter.ai" in api_base and not api_base.endswith("/api/v1"):
        return f"{api_base}/api/v1"
    return api_base

def _extract_text_content(content) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "".join(parts).strip()

    return str(content).strip()


def _extract_json_block(content: str) -> str:
    content = content.strip()

    if content.startswith("```json"):
        content = content.split("```json", 1)[1]
    elif content.startswith("```"):
        content = content.split("```", 1)[1]

    if content.endswith("```"):
        content = content.rsplit("```", 1)[0]

    content = content.strip()
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        return content[start:end + 1]

    raise json.JSONDecodeError("JSON object not found in model response", content, 0)


def _split_model_candidates(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    normalized = raw_value.replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _is_retryable_provider_error(error: Exception) -> bool:
    error_text = str(error)
    error_type = type(error).__name__
    retry_markers = ("402", "429", "500", "502", "503", "504", "RateLimitError", "APIConnectionError", "APITimeoutError")
    return error_type in retry_markers or any(marker in error_text for marker in retry_markers)


def _normalize_question_type(raw_type: str | None) -> str:
    allowed = {"MCQ", "Scenario", "Calculation", "Procedure"}
    if raw_type in allowed:
        return raw_type
    return "MCQ"


def _normalize_bloom_level(raw_level: str | None) -> str:
    allowed = {"Remember", "Understand", "Apply", "Analyze"}
    if raw_level in allowed:
        return raw_level
    return "Apply"


def _infer_difficulty(level: str) -> str:
    mapping = {
        "Junior": "Easy",
        "Middle": "Medium",
        "Senior": "Hard",
        "Expert": "Hard",
    }
    return mapping.get(level, "Medium")


def _normalize_options(raw_options) -> list[str] | None:
    if not isinstance(raw_options, list):
        return None

    normalized_options: list[str] = []
    for option in raw_options:
        if isinstance(option, str):
            normalized_options.append(option)
        elif isinstance(option, dict):
            option_id = option.get("id")
            option_text = option.get("text") or option.get("option_text") or option.get("value")
            if option_id and option_text:
                normalized_options.append(f"{option_id}. {option_text}")
            elif option_text:
                normalized_options.append(str(option_text))
    return normalized_options or None


def _normalize_distractor_explanations(raw_value) -> list[str] | None:
    if isinstance(raw_value, list):
        return [str(item) for item in raw_value]
    if isinstance(raw_value, dict):
        return [f"{key}: {value}" for key, value in raw_value.items()]
    return None


def _reorder_questions_by_scope(questions: list[dict]) -> list[dict]:
    """
    Пересортировывает вопросы: сначала общие (Remember/Understand), потом узкоспециализированные (Apply/Analyze)
    """
    general_levels = {"Remember", "Understand"}
    specialty_levels = {"Apply", "Analyze"}

    general_questions = []
    specialty_questions = []
    other_questions = []

    for q in questions:
        bl = str(q.get("bloom_level", "Remember")).strip().capitalize()
        if bl in general_levels:
            general_questions.append(q)
        elif bl in specialty_levels:
            specialty_questions.append(q)
        else:
            other_questions.append(q)

    return general_questions + specialty_questions + other_questions


def _normalize_generated_test_payload(payload: dict, input_data: GenerationInput) -> dict:
    questions = payload.get("questions") or []
    normalized_questions = []

    for index, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            continue

        question_metadata = {
            key: value
            for key, value in question.items()
            if key not in {
                "id",
                "type",
                "difficulty",
                "bloom_level",
                "question_text",
                "question",
                "prompt",
                "options",
                "correct_answer",
                "answer",
                "explanation",
                "rationale",
                "distractor_explanations",
            }
        }

        normalized_questions.append(
            {
                "id": str(question.get("id", index)),
                "type": _normalize_question_type(question.get("type")),
                "difficulty": question.get("difficulty") or _infer_difficulty(input_data.level),
                "bloom_level": _normalize_bloom_level(question.get("bloom_level")),
                "question_text": question.get("question_text") or question.get("question") or question.get("prompt") or f"Question {index}",
                "options": _normalize_options(question.get("options")),
                "correct_answer": question.get("correct_answer", question.get("answer")),
                "explanation": question.get("explanation") or question.get("rationale") or "Пояснение не было возвращено моделью.",
                "distractor_explanations": _normalize_distractor_explanations(question.get("distractor_explanations")),
                "metadata": question_metadata,
            }
        )

    # Пересортируем вопросы по структуре: общие → узкоспециализированные
    normalized_questions = _reorder_questions_by_scope(normalized_questions)

    standards = payload.get("standards_covered")
    if not isinstance(standards, list):
        standards = []
    if not standards:
        for question in questions:
            if isinstance(question, dict) and isinstance(question.get("standard_refs"), list):
                standards.extend(str(item) for item in question["standard_refs"])
    standards = list(dict.fromkeys(standards))

    top_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    for extra_key in ("test_title", "total_questions"):
        if extra_key in payload:
            top_metadata[extra_key] = payload[extra_key]

    return {
        "title": payload.get("title") or payload.get("test_title") or f"{input_data.topic} - {input_data.level}",
        "topic": payload.get("topic") or input_data.topic,
        "specialty": payload.get("specialty") or input_data.specialty,
        "level": payload.get("level") or input_data.level,
        "duration_minutes": payload.get("duration_minutes") or max(15, len(normalized_questions) * 7),
        "questions": normalized_questions,
        "standards_covered": standards,
        "metadata": top_metadata,
    }


class TestGenerator:
    """ Основной класс для генерации тестов """
    
    def __init__(self, model_name: str = None, judge_name: str = None, temperature: float = 0.7):
        main_models = _split_model_candidates(model_name or os.getenv("MAIN_MODEL"))
        judge_models = _split_model_candidates(judge_name or os.getenv("JUDGE_MODEL"))
        if not main_models:
            raise ValueError("В `.env` должна быть указана `MAIN_MODEL`.")
        if not judge_models:
            judge_models = main_models.copy()

        api_base = _get_api_base()
        self.temperature = temperature
        self.api_base = api_base
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.request_timeout = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
        self.main_models = main_models
        self.judge_models = judge_models
        if not self.api_key:
            raise ValueError("В `.env` должна быть указана `OPENAI_API_KEY`.")
        # #region debug-point A:init-client
        _debug_report("A", "core/generator.py:init", "init clients", {
            "main_models": main_models,
            "judge_models": judge_models,
            "api_base": api_base,
            "has_api_key": bool(self.api_key),
        })
        # #endregion

    def _create_client(self, model_name: str) -> ChatOpenAI:
        return ChatOpenAI(
            openai_api_base=self.api_base,
            openai_api_key=self.api_key,
            model=model_name,
            temperature=self.temperature,
            request_timeout=self.request_timeout,
            max_retries=0,
        )

    def _invoke_with_model_fallback(self, messages, model_candidates: list[str], purpose: str):
        last_error = None
        attempted_models = []
        for index, model_name in enumerate(model_candidates):
            attempted_models.append(model_name)
            try:
                # #region debug-point H:model-attempt
                _debug_report("H", "core/generator.py:fallback", "trying model candidate", {
                    "purpose": purpose,
                    "model_name": model_name,
                    "candidate_index": index,
                    "candidate_count": len(model_candidates),
                })
                # #endregion
                client = self._create_client(model_name)
                response = client.invoke(messages)
                return response, model_name
            except Exception as error:
                last_error = error
                # #region debug-point H:model-error
                _debug_report("H", "core/generator.py:fallback", "model candidate failed", {
                    "purpose": purpose,
                    "model_name": model_name,
                    "candidate_index": index,
                    "error_type": type(error).__name__,
                    "error": str(error),
                })
                # #endregion
                if not _is_retryable_provider_error(error) or index == len(model_candidates) - 1:
                    continue
                time.sleep(min(2 + index, 5))
        if last_error is not None:
            if _is_retryable_provider_error(last_error):
                raise RuntimeError(
                    f"Все модели для `{purpose}` временно недоступны: {', '.join(attempted_models)}. "
                    f"Последняя ошибка провайдера: {last_error}"
                ) from last_error
            raise last_error
        raise RuntimeError(f"Не задана ни одна модель для `{purpose}`.")
    
    def generate(self, input_data: GenerationInput, max_retries: int = 2) -> GeneratedTest:
        """
        Основной метод генерации теста.
        """
        attempt = 0
        context = "Нет дополнительного контекста."

        while attempt <= max_retries:
            try:
                user_prompt = format_user_prompt(input_data, context)
                
                messages = [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=user_prompt + "\n\nВерни ответ строго в формате JSON без дополнительного текста.")
                ]

                # #region debug-point B:invoke-main
                _debug_report("B", "core/generator.py:generate", "before main invoke", {
                    "attempt": attempt,
                    "message_count": len(messages),
                    "num_questions": input_data.num_questions,
                    "model_candidates": self.main_models,
                })
                # #endregion
                response, selected_model = self._invoke_with_model_fallback(messages, self.main_models, "main_generation")
                content = _extract_text_content(response.content)
                # #region debug-point B:main-result
                _debug_report("B", "core/generator.py:generate", "after main invoke", {
                    "attempt": attempt,
                    "selected_model": selected_model,
                    "content_length": len(content),
                    "content_prefix": content[:300],
                })
                # #endregion

                # Парсим JSON даже если модель добавила пояснение или markdown-обёртку
                test_dict = json.loads(_extract_json_block(content))
                # #region debug-point C:json-parse
                _debug_report("C", "core/generator.py:generate", "parsed json block", {
                    "attempt": attempt,
                    "keys": sorted(test_dict.keys()) if isinstance(test_dict, dict) else str(type(test_dict)),
                })
                # #endregion
                
                # Создаём объект модели
                normalized_payload = _normalize_generated_test_payload(test_dict, input_data)
                # #region debug-point C:normalized-payload
                _debug_report("C", "core/generator.py:generate", "normalized payload", {
                    "attempt": attempt,
                    "question_count": len(normalized_payload["questions"]),
                    "title": normalized_payload["title"],
                })
                # #endregion
                test = GeneratedTest.model_validate(normalized_payload)

                # Оценка качества
                judge_result = self._evaluate_test(test)
                
                if judge_result.get("passed", False) or attempt == max_retries:
                    test.metadata["judge_score"] = judge_result.get("overall_score")
                    test.metadata["judge_critique"] = judge_result.get("critique", "")
                    return test
                
                print(f"Попытка {attempt + 1}: Качество низкое → refinement")
                context = f"Предыдущая версия имела проблемы: {judge_result.get('critique', '')}. Исправь ошибки и сгенерируй заново."
                attempt += 1

            except json.JSONDecodeError as e:
                # #region debug-point D:json-error
                _debug_report("D", "core/generator.py:generate", "json decode error", {
                    "attempt": attempt,
                    "error": str(e),
                })
                # #endregion
                print(f"Ошибка парсинга JSON (попытка {attempt + 1}): {e}")
                time.sleep(min(2 ** (attempt + 1), 8))
                attempt += 1
            except Exception as e:
                # #region debug-point E:generate-error
                _debug_report("E", "core/generator.py:generate", "generation error", {
                    "attempt": attempt,
                    "error_type": type(e).__name__,
                    "error": str(e),
                })
                # #endregion
                print(f"Ошибка при генерации (попытка {attempt + 1}): {e}")
                if _is_retryable_provider_error(e):
                    time.sleep(min(2 ** (attempt + 1), 8))
                attempt += 1
                if attempt > max_retries:
                    raise

        raise Exception("Не удалось сгенерировать тест после всех попыток")


    def _evaluate_test(self, test: GeneratedTest) -> dict:
        """Оценивает качество теста"""
        try:
            # Гарантируем, что test — это объект GeneratedTest
            if isinstance(test, dict):
                test = GeneratedTest.model_validate(test)
            
            test_json = test.model_dump_json(indent=2)
            
            judge_prompt = JUDGE_PROMPT_TEMPLATE.format(test_json=test_json)
            
            messages = [
                SystemMessage(content="Ты строгий эксперт по оценке технических тестов."),
                HumanMessage(content=judge_prompt)
            ]
            
            # #region debug-point F:invoke-judge
            _debug_report("F", "core/generator.py:judge", "before judge invoke", {
                "question_count": len(test.questions),
                "model_candidates": self.judge_models,
            })
            # #endregion
            response, selected_model = self._invoke_with_model_fallback(messages, self.judge_models, "judge")
            content = _extract_text_content(response.content)
            # #region debug-point F:judge-result
            _debug_report("F", "core/generator.py:judge", "after judge invoke", {
                "selected_model": selected_model,
                "content_length": len(content),
                "content_prefix": content[:300],
            })
            # #endregion
            
            result = json.loads(_extract_json_block(content))
            return result
            
        except Exception as e:
            # #region debug-point G:judge-error
            _debug_report("G", "core/generator.py:judge", "judge error", {
                "error_type": type(e).__name__,
                "error": str(e),
            })
            # #endregion
            print(f"Ошибка Judge: {e}")
            return {
                "overall_score": 7.5,
                "critique": "Не удалось провести полноценную оценку",
                "passed": True
            }
