FROM python:3.13-slim

RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn>=0.30" \
 && useradd --create-home --uid 1000 mock

WORKDIR /app
COPY mock_llm.py .
USER mock
EXPOSE 8000
CMD ["python", "mock_llm.py"]
