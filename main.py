"""
CLI-интерфейс для LLM-core
"""

import typer
from rich.console import Console
from rich.panel import Panel
from rich import print as rprint
from dotenv import load_dotenv
from core.models import GenerationInput
from core.generator import TestGenerator
import json
from datetime import datetime

load_dotenv(override=True)

app = typer.Typer(help="LLM-core — генератор тестов для нефтегазовой отрасли")
console = Console()

@app.callback()
def main() -> None:
    """Корневая CLI-группа для команд генератора."""
    pass

@app.command()
def generate(
    topic: str = typer.Option(..., "--topic", "-t", help="Тема теста, например: \"Контроль скважины\""),
    specialty: str = typer.Option(..., "--specialty", "-s", help="Специальность, например: \"Инженер по бурению\""),
    level: str = typer.Option("Senior", "--level", "-l", help="Уровень: Junior, Middle, Senior, Expert"),
    num_questions: int = typer.Option(5, "--num", "-n", help="Количество вопросов", min=3, max=50),
    additional_topics: list[str] = typer.Option(None, "--additional-topic", "-at", help="Дополнительные темы, которые должны присутствовать в тесте (можно указать несколько раз)"),
    batch_size: int = typer.Option(5, "--batch-size", "-b", help="Количество вопросов в одном батче", min=1),
    max_workers: int = typer.Option(4, "--max-workers", "-w", help="Количество параллельных потоков", min=1),
    skip_judge: bool = typer.Option(False, "--skip-judge", help="Пропустить оценку качества теста (ускоряет инференс)"),
    output: str = typer.Option(None, "--output", "-o", help="Сохранить результат в JSON-файл"),
):
    """
    Генерирует тест по заданным параметрам с батчевым инференсом.
    """
    import time
    start_time = time.time()
    
    try:
        topics_display = f"\nДополнительные темы: [cyan]{', '.join(additional_topics)}[/cyan]" if additional_topics else ""
        console.print(Panel.fit(
            f"[bold blue]Генерация теста[/bold blue]\n"
            f"Тема: [cyan]{topic}[/cyan]\n"
            f"Специальность: [cyan]{specialty}[/cyan]\n"
            f"Уровень: [cyan]{level}[/cyan] | Вопросов: [cyan]{num_questions}[/cyan]\n"
            f"Батч: [cyan]{batch_size}[/cyan] | Потоки: [cyan]{max_workers}[/cyan]\n"
            f"Judge: [cyan]{'Пропущен' if skip_judge else 'Включен'}[/cyan]"
            f"{topics_display}",
            title="LLM-core"
        ))

        # Подготовка входных данных
        input_data = GenerationInput(
            topic=topic,
            specialty=specialty,
            level=level,
            num_questions=num_questions,
            additional_topics=additional_topics,
        )

        # Генерация теста
        generator = TestGenerator(batch_size=batch_size, max_workers=max_workers)
        generation_start = time.time()
        test = generator.generate(input_data, skip_judge=skip_judge)
        generation_time = time.time() - generation_start

        # Вывод результата
        total_time = time.time() - start_time
        rprint(f"\n[bold green]✅ Тест успешно сгенерирован![/bold green] (ID: {test.test_id})")
        rprint(f"Название: [bold]{test.title}[/bold]")
        rprint(f"Вопросов: {len(test.questions)} | Judge Score: {test.metadata.get('judge_score', 'N/A')}")
        rprint(f"[dim]⏱️  Время генерации: {generation_time:.1f} сек | Общее время: {total_time:.1f} сек[/dim]\n")

        # Показываем первые 2 вопроса в консоли
        for i, q in enumerate(test.questions[:2], 1):
            console.print(Panel(
                f"[bold]Вопрос {i}:[/bold] {q.question_text[:300]}...\n\n"
                f"[dim]Тип: {q.type} | Сложность: {q.difficulty} | Bloom: {q.bloom_level}[/dim]",
                title=f"Пример вопроса {i}",
                border_style="blue"
            ))

        # Сохранение в файл
        if output:
            filename = output if output.endswith(".json") else f"{output}.json"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(test.model_dump_json(indent=2))
            rprint(f"[green]Тест сохранён в файл:[/green] {filename}")

        return test

    except Exception as e:
        console.print(f"[bold red]Ошибка:[/bold red] {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
