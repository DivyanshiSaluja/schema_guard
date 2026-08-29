SYSTEM_PROMPT = """You are an ETL repair agent. You will be given:
1. A schema drift description
2. The current transformation function

Return ONLY a valid Python function named `transform(row: dict) -> dict`
that correctly handles the new schema. No explanation, no markdown fences."""

def build_prompt(diff, current_code, error_log=None):
    prompt = f"Schema change:\n{diff}\n\nCurrent code:\n{current_code}"
    if error_log:
        prompt += f"\n\nYour previous attempt failed with this error:\n{error_log}\nFix it."
    return prompt