"""Versioned prompt constants.

Prompts are versioned so evaluation runs are reproducible (the prompt version
is recorded alongside the model ID and scenario seed).
"""

PROMPT_VERSION = "v0"

#: Placeholder prompt templates — populated during the feasibility spike.
PARSE_REQUEST = "prompts.parse_request/{PROMPT_VERSION}"
PROPOSE_RESEARCH_STEPS = "prompts.propose_research_steps/{PROMPT_VERSION}"
EXPLAIN_RECOMMENDATION = "prompts.explain_recommendation/{PROMPT_VERSION}"
