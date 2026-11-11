# Ask the Data

Ask a question about humanitarian statistics in plain English, get a validated SQL query, run it read-only on SQLite, and see the result. Accuracy is measured on a hand-written evaluation set.

> **Status: work in progress.** This README is a stub. Results, architecture diagram and full documentation will be added once the evaluation has been run. No accuracy numbers are reported yet.

## Setup (so far)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env    # then add your Gemini API key to .env
pytest
```

## Checking the model name

The model id lives in `configs/config.yaml` (`model.name`). Model ids change over time. To see which ones your key can use:

```python
from dotenv import load_dotenv
from google import genai

load_dotenv()  # copies GEMINI_API_KEY from .env into the environment
client = genai.Client()  # reads GEMINI_API_KEY from the environment
for model in client.models.list():
    print(model.name)
```

You can also check the models page in the Gemini API documentation or in Google AI Studio.

## License

Code: MIT (see `LICENSE`). Data sources and their licences: see `data/sources.md`.
