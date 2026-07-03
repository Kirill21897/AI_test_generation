#!/usr/bin/env python3
"""Проверка импортов"""

print("Проверка импортов...")
try:
    from core.generator import TestGenerator
    from core.models import GenerationInput
    print("✅ Импорты успешны!")
except Exception as e:
    print(f"❌ Ошибка импорта: {e}")
    import traceback
    traceback.print_exc()
