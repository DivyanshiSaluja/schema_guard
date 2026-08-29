from dotenv import load_dotenv
load_dotenv()
import openai, os
from src.common.models import RepairCandidate
from src.agent.prompts import SYSTEM_PROMPT, build_prompt
from src.agent.agent_tools import read_pipeline_source, write_candidate_file
from src.sandbox.docker_runner import run_in_sandbox

client = openai.OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY"),
)

def call_llm(prompt: str) -> str:
    resp = client.chat.completions.create(
        model="qwen/qwen3.8-27b",
        max_tokens=500,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    text = resp.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.replace("python", "", 1).strip() if text.startswith("python") else text.strip()
    return text

def generate_and_verify_candidate(diff, max_retries=2) -> RepairCandidate:
    current_code = read_pipeline_source()
    error_log = None
    candidate_id = f"cand_{diff.old_column}_{diff.new_column}"

    for attempt in range(1, max_retries + 2):
        print(f"[agent] attempt {attempt}: proposing candidate for {diff}")
        prompt = build_prompt(diff, current_code, error_log)
        code = call_llm(prompt)
        write_candidate_file(candidate_id, code)

        print(f"[agent] calling tool: run_in_sandbox({candidate_id})")
        result = run_in_sandbox(candidate_id, code)

        if result.passed:
            print(f"[agent] candidate verified on attempt {attempt}")
            return RepairCandidate(
                id=candidate_id, code=code,
                explanation=f"Repairs {diff.change_type} on {diff.old_column or diff.new_column}",
                passed_sandbox=True, attempt=attempt, sandbox_log=result.log,
            )
        else:
            print(f"[agent] attempt {attempt} FAILED: {result.log}")
            error_log = result.log

    return RepairCandidate(
        id=candidate_id, code=code, explanation="All attempts failed",
        passed_sandbox=False, attempt=max_retries + 1, sandbox_log=error_log,
    )