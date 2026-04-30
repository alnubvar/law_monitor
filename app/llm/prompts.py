from __future__ import annotations


def build_analysis_prompt(title: str, raw_text: str) -> str:
    return f"""
Ты анализируешь документ для GR-мониторинга агрохолдинга в сфере АПК.

Верни строго JSON следующего вида:
{{
  "is_relevant": true,
  "relevance_reason": "...",
  "topic": "...",
  "importance": "high|medium|low",
  "summary": "...",
  "impact": "...",
  "key_dates": [],
  "regions": [],
  "source_facts": []
}}

Правила:
- не придумывай факты;
- если информации нет, указывай null или пустой список;
- опирайся только на текст документа;
- обязательно объясняй связь с АПК и потенциальное влияние на агрохолдинг;
- source_facts должны быть краткими фактами только из исходного текста;
- если документ нерелевантен, это тоже нужно явно отразить.

Заголовок:
{title}

Текст документа:
{raw_text}
""".strip()

