"""Offline eval set and live runner that measure which tool the Concierge's model chooses.

``cases.jsonl`` holds the versioned prompts with their expected tools. ``runner`` builds the
real Concierge agent over the Gateway's tool schemas with stub executors and scores the model's
choices. Nothing here calls Amazon Bedrock unless ``scripts/run_tool_choice_eval.py`` is run with
both confirmation flags; the unit tests exercise every other part offline.
"""
