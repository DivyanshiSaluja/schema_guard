import openai, os
from dotenv import load_dotenv
load_dotenv()

client = openai.OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.getenv("NVIDIA_API_KEY"),
)
resp = client.chat.completions.create(
    model="nemotron-3.5-lightning-30b-a3b",
    messages=[{"role": "user", "content": "Say hello in one word."}],
)
print(resp.choices[0].message.content)