"""自媒体 + 商城 周报 (weekly media & e-commerce report).

app/reports/weekly_media.py   — deterministic aggregation (no LLM, unit-testable)
app/reports/narrative.py      — optional one-paragraph LLM narration on top
app/reports/render.py         — Jinja2 HTML rendering
app/reports/service.py        — orchestration: build -> narrate -> render -> persist -> notify
"""
