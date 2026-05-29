import os
import re

handlers_dir = "handlers"
files = [f for f in os.listdir(handlers_dir) if f.endswith(".py") and f != "__init__.py"]

for filename in files:
    filepath = os.path.join(handlers_dir, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Заменяем абсолютные импорты внутри пакета handlers на относительные
    # Например: from catalog import ... -> from .catalog import ...
    # Исключаем импорты, которые не являются модулями handlers (db, config и т.д.)
    def replace_import(match):
        module = match.group(1)
        # Если модуль находится в папке handlers, добавляем точку
        if module + ".py" in files:
            return f"from .{module} import"
        # Иначе оставляем как есть (это внешний модуль)
        return match.group(0)

    content = re.sub(r"from (catalog|start|cart|checkout|fsm_inputs|posts|admin|orders) import", replace_import, content)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Исправлен файл: {filepath}")