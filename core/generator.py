""" Основной LLM-core """
from __future__ import annotations

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from .models import GenerationInput, GeneratedTest
from .prompts import SYSTEM_PROMPT, JUDGE_PROMPT_TEMPLATE, format_user_prompt
import json
import os
import re
import time
import urllib.request
import concurrent.futures
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
    
    # Если контент уже выглядит как JSON, сразу возвращаем
    if content.startswith("{") and content.endswith("}"):
        try:
            json.loads(content)
            return content
        except:
            pass
            
    # Извлекаем из markdown блоков
    
    # Попробуем найти ```json ... ```
    match = re.search(r"```json\s*([\s\S]*?)\s*```", content)
    if match:
        content = match.group(1).strip()
    
    # Если не нашли, попробуем просто ``` ... ```
    if not (content.startswith("{") and content.endswith("}")):
        match = re.search(r"```\s*([\s\S]*?)\s*```", content)
        if match:
            content = match.group(1).strip()
    
    # Находим первую { и последнюю }
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_str = content[start:end + 1].strip()
        
        # Попробуем исправить одиночные кавычки
        json_str = re.sub(r"'([^']*?)'", r'"\1"', json_str)
        
        # Удаляем trailing commas
        json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
        
        # Удаляем комментарии
        json_str = re.sub(r"//.*$", "", json_str, flags=re.MULTILINE)
        json_str = re.sub(r"/\*.*?\*/", "", json_str, flags=re.DOTALL)
        
        return json_str
    
    raise json.JSONDecodeError("JSON object not found in model response", content, 0)


def _split_model_candidates(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    normalized = raw_value.replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _split_into_batches(total_questions: int, batch_size: int = 5) -> list[int]:
    """Разбивает общее количество вопросов на батчи."""
    batches = []
    remaining = total_questions
    while remaining > 0:
        current_batch = min(batch_size, remaining)
        batches.append(current_batch)
        remaining -= current_batch
    return batches


def _generate_single_batch(
    generator_instance: 'TestGenerator',
    input_data: GenerationInput,
    batch_questions: int,
    attempt: int,
    context: str,
    batch_index: int
) -> dict:
    """Генерирует один батч вопросов (для параллельного выполнения)."""
    max_batch_retries = 2
    batch_attempt = 0
    
    while batch_attempt <= max_batch_retries:
        try:
            # Создаём копию входных данных для этого батча
            batch_input = GenerationInput(
                topic=input_data.topic,
                specialty=input_data.specialty,
                level=input_data.level,
                num_questions=batch_questions,
                subdomain=input_data.subdomain,
                additional_context=input_data.additional_context
            )
            
            user_prompt = format_user_prompt(batch_input, context)
            
            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_prompt + "\n\nВерни ответ строго в формате JSON без дополнительного текста.")
            ]
            
            # Debug-логирование перед вызовом
            _debug_report(f"B{batch_index}", "core/generator.py:batch", f"before batch {batch_index} invoke", {
                "attempt": batch_attempt,
                "batch_questions": batch_questions,
                "model_candidates": generator_instance.main_models,
            })
            
            response, selected_model = generator_instance._invoke_with_model_fallback(
                messages, 
                generator_instance.main_models, 
                f"batch_{batch_index}_generation"
            )
            content = _extract_text_content(response.content)
            
            # Debug-логирование после вызова
            _debug_report(f"B{batch_index}", "core/generator.py:batch", f"after batch {batch_index} invoke", {
                "selected_model": selected_model,
                "content_length": len(content),
            })
            
            # Парсим JSON
            try:
                test_dict = json.loads(_extract_json_block(content))
            except Exception as e:
                # Если не удалось распарсить, выводим ответ для отладки
                print(f"\n⚠️  Не удалось распарсить JSON. Ответ модели (батч {batch_index}):\n{content[:2000]}\n")
                raise e
            
            # Нормализуем payload
            normalized = _normalize_generated_test_payload(test_dict, batch_input)
            
            return {
                "success": True,
                "batch_index": batch_index,
                "normalized": normalized,
                "selected_model": selected_model
            }
            
        except json.JSONDecodeError as e:
            _debug_report(f"B{batch_index}", "core/generator.py:batch", f"JSON decode error in batch {batch_index}", {
                "attempt": batch_attempt,
                "error": str(e),
            })
            print(f"Батч {batch_index}: Ошибка парсинга JSON (попытка {batch_attempt + 1}): {e}")
            time.sleep(min(2 ** (batch_attempt + 1), 4))
            batch_attempt += 1
            
        except Exception as e:
            _debug_report(f"B{batch_index}", "core/generator.py:batch", f"Error in batch {batch_index}", {
                "attempt": batch_attempt,
                "error_type": type(e).__name__,
                "error": str(e),
            })
            print(f"Батч {batch_index}: Ошибка (попытка {batch_attempt + 1}): {e}")
            if _is_retryable_provider_error(e):
                time.sleep(min(2 ** (batch_attempt + 1), 4))
            batch_attempt += 1
            if batch_attempt > max_batch_retries:
                return {
                    "success": False,
                    "batch_index": batch_index,
                    "error": str(e)
                }
    
    return {
        "success": False,
        "batch_index": batch_index,
        "error": f"Не удалось сгенерировать батч после {max_batch_retries + 1} попыток"
    }


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


def _normalize_generated_test_payload(payload: dict, input_data: GenerationInput) -> dict:
    questions = payload.get("questions") or []
    normalized_questions = []

    for index, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            continue

        # Упрощённая нормализация вопроса
        q_type = question.get("type", "MCQ")
        if q_type not in ["MCQ", "Scenario", "Calculation", "Procedure"]:
            q_type = "MCQ"
            
        difficulty = question.get("difficulty", _infer_difficulty(input_data.level))
        if difficulty not in ["Easy", "Medium", "Hard"]:
            difficulty = _infer_difficulty(input_data.level)
            
        bloom_level = question.get("bloom_level", "Apply")
        if bloom_level not in ["Remember", "Understand", "Apply", "Analyze"]:
            bloom_level = "Apply"
            
        options = question.get("options")
        if not isinstance(options, list):
            options = None
        else:
            # Убеждаемся, что options - это список строк
            options = [str(opt) for opt in options if opt]
            if not options:
                options = None

        normalized_questions.append(
            {
                "id": str(question.get("id", index)),
                "type": q_type,
                "difficulty": difficulty,
                "bloom_level": bloom_level,
                "question_text": str(question.get("question_text", question.get("question", f"Question {index}"))),
                "options": options,
                "correct_answer": str(question.get("correct_answer", question.get("answer", ""))),
                "explanation": str(question.get("explanation", question.get("rationale", "No explanation provided."))),
                "distractor_explanations": None,
                "metadata": {},
            }
        )

    standards = payload.get("standards_covered", [])
    if not isinstance(standards, list):
        standards = []
    standards = [str(s) for s in standards if s]
    standards = list(dict.fromkeys(standards))

    return {
        "title": str(payload.get("title", f"{input_data.topic} - {input_data.level}")),
        "topic": str(payload.get("topic", input_data.topic)),
        "specialty": str(payload.get("specialty", input_data.specialty)),
        "level": str(payload.get("level", input_data.level)),
        "duration_minutes": int(payload.get("duration_minutes", max(15, len(normalized_questions) * 7))),
        "questions": normalized_questions,
        "standards_covered": standards,
        "metadata": {},
    }


class TestGenerator:
    """ Основной класс для генерации тестов """
    
    def __init__(self, model_name: str = None, judge_name: str = None, temperature: float = 0.7, batch_size: int = None, max_workers: int = None):
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
        self.batch_size = batch_size or int(os.getenv("BATCH_SIZE", "5"))
        self.max_workers = max_workers or int(os.getenv("MAX_WORKERS", "4"))
        if not self.api_key:
            raise ValueError("В `.env` должна быть указана `OPENAI_API_KEY`.")
        # #region debug-point A:init-client
        _debug_report("A", "core/generator.py:init", "init clients", {
            "main_models": main_models,
            "judge_models": judge_models,
            "api_base": api_base,
            "has_api_key": bool(self.api_key),
            "batch_size": batch_size,
            "max_workers": max_workers
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
    
    def generate(self, input_data: GenerationInput, max_retries: int = 2, skip_judge: bool = False) -> GeneratedTest:
        """
        Основной метод генерации теста с параллельным батчевым инференсом.
        """
        attempt = 0
        context = "Нет дополнительного контекста."

        while attempt <= max_retries:
            try:
                # Разбиваем на батчи
                batches = _split_into_batches(input_data.num_questions, self.batch_size)
                print(f"🚀 Генерация {input_data.num_questions} вопросов в {len(batches)} батчах (по {self.batch_size}) с {self.max_workers} параллельными потоками...")

                # Генерируем батчи параллельно
                all_questions = []
                all_standards = []
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = []
                    for batch_idx, batch_questions in enumerate(batches):
                        future = executor.submit(
                            _generate_single_batch,
                            self,
                            input_data,
                            batch_questions,
                            attempt,
                            context,
                            batch_idx
                        )
                        futures.append(future)
                    
                    # Собираем результаты
                    for future in concurrent.futures.as_completed(futures):
                        result = future.result()
                        if result["success"]:
                            normalized = result["normalized"]
                            all_questions.extend(normalized["questions"])
                            all_standards.extend(normalized["standards_covered"])
                            print(f"✅ Батч {result['batch_index']} готов ({len(normalized['questions'])} вопросов)")
                        else:
                            print(f"❌ Ошибка в батче {result['batch_index']}: {result['error']}")
                
                if not all_questions:
                    raise Exception("Не удалось сгенерировать ни одного вопроса из всех батчей")
                
                # Удаляем дубликаты стандартов
                all_standards = list(dict.fromkeys(all_standards))
                
                # Формируем финальный payload
                final_payload = {
                    "title": f"{input_data.topic} - {input_data.level}",
                    "topic": input_data.topic,
                    "specialty": input_data.specialty,
                    "level": input_data.level,
                    "duration_minutes": max(15, len(all_questions) * 7),
                    "questions": all_questions,
                    "standards_covered": all_standards,
                    "metadata": {
                        "batches_used": len(batches),
                        "batch_size": self.batch_size,
                        "total_questions_generated": len(all_questions)
                    }
                }
                
                test = GeneratedTest.model_validate(final_payload)

                # Оценка качества (если не пропущено)
                if skip_judge:
                    print("⏭️  Оценка качества пропущена (--skip-judge)")
                    test.metadata["judge_score"] = "Пропущено"
                    test.metadata["judge_critique"] = "Оценка не проводилась"
                    test.metadata["batches_used"] = len(batches)
                    test.metadata["batch_size"] = self.batch_size
                    return test
                
                judge_result = self._evaluate_test(test)
                
                if judge_result.get("passed", False) or attempt == max_retries:
                    test.metadata["judge_score"] = judge_result.get("overall_score")
                    test.metadata["judge_critique"] = judge_result.get("critique", "")
                    test.metadata["batches_used"] = len(batches)
                    test.metadata["batch_size"] = self.batch_size
                    return test
                
                print(f"🔄 Попытка {attempt + 1}: Качество низкое → refinement")
                context = f"Предыдущая версия имела проблемы: {judge_result.get('critique', '')}. Исправь ошибки и сгенерируй заново."
                attempt += 1

            except Exception as e:
                print(f"❌ Ошибка при генерации (попытка {attempt + 1}): {e}")
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
