FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv pip install --system --no-cache -e .

COPY . .

CMD ["uvicorn", "cold_mailer.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
