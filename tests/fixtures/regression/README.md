# Regression Fixtures

Эти fixtures фиксируют реальные и репрезентативные кейсы AHSTEP GR-monitoring, чтобы изменения rule engine не меняли поведение незаметно.

Зачем это нужно:
- ловить регрессии в `action_level`, `page_type`, `source_role` и `business_signal`
- проверять rule engine на реальных сценариях, а не только на точечных unit-тестах
- сохранять стабильность `requires_attention`, watchlist/background split и source taxonomy

Как добавить новый fixture:
1. Создайте новый `*.json` в этой папке.
2. Используйте уникальный `id`.
3. Укажите реальные или репрезентативные поля:
   `id`, `source_name`, `source_role`, `title`, `url`, `raw_text`,
   `expected_action_level`, `expected_page_type`, `expected_source_role`.
4. При необходимости добавьте:
   `level`, `region`, `summary`, `expected_business_signal_contains`, `expected_not_action_level`.
5. Берите ожидания только из текущего фактического поведения системы, а не из желаемого поведения.

Ограничения:
- не кладите секреты, токены, куки, приватные URL или персональные данные
- не используйте внутренние документы с ограниченным доступом
- не меняйте старые fixtures без явной причины и описания регрессии, которую вы фиксируете
