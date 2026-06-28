# Card Audit Agent Docs

Минимальный пакет документов для fresh-агентов карточного аудита Vital
Shevron.

## Назначение

Этот пакет нужен, чтобы агент получил ровно достаточно контекста для своей
роли и не читал весь проект. Документы не заменяют корневые runbook-и, но
дают безопасный минимум для одной карточки.

## Файлы

- `card_auditor_minimal_prompt.md` - задача для fresh-аудитора одной карточки.
- `card_validator_minimal_prompt.md` - задача для fresh-проверяющего результата.
- `card_audit_minimal_rules.md` - краткие правила заполнения карточки.
- `card_audit_output_contract.md` - формат HTML/JSON результата слоя 2.
- `card_audit_html_template.html` - owner-approved HTML-шаблон карточного
  аудита: desktop/mobile формат, карточки рекомендаций, встроенный коллаж,
  полные параметры Ozon/WB в `details`.

## Как использовать

Оркестратор передает аудитору:

1. `card_auditor_minimal_prompt.md`;
2. `card_audit_minimal_rules.md`;
3. `card_audit_output_contract.md`;
4. `card_audit_html_template.html`;
5. входной пакет одной карточки из слоя 1;
6. ссылки на фото/коллаж/`photos.html`, если есть.

Оркестратор передает проверяющему:

1. `card_validator_minimal_prompt.md`;
2. `card_audit_minimal_rules.md`;
3. `card_audit_output_contract.md`;
4. HTML/JSON результата аудитора;
5. входной пакет карточки, если нужен для сверки.

## Ограничения

- Один fresh-аудитор работает только с одной карточкой и не переиспользуется.
- Один fresh-проверяющий проверяет один результат и не переиспользуется.
- Агент не меняет карточки Ozon/WB, не готовит apply и не пишет в слой 3.
- Если данных не хватает, агент пишет `not_confirmed` и не выдумывает.
- Если схема неудобна, неполна или может быть автоматизирована лучше, агент
  обязан сообщить оркестратору, что именно нужно улучшить.

## Слой данных

```text
Layer 1: data/catalog/content/       source marketplace data
Layer 2: data/catalog/card_audits/   agent audit HTML/JSON
Layer 3: data/catalog/master_passport/ owner-approved final passport
```

Аудитор и проверяющий работают только на подготовку слоя 2.
