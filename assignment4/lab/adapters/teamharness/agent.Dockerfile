FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
# The unchanged course preflight imports OpenAI for models.list. The team
# runtime carries its pure Python schema validator; the relay uses the stdlib.
RUN pip install --no-cache-dir openai==3.37.0
