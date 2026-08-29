import inspect
from src.pipeline import transformations

def read_pipeline_source() -> str:
    return inspect.getsource(transformations)

def write_candidate_file(candidate_id: str, code: str) -> str:
    path = f"data/candidates/{candidate_id}.py"
    import os
    os.makedirs("data/candidates", exist_ok=True)
    with open(path, "w") as f:
        f.write(code)
    return path