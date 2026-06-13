FROM python:3.12-slim

WORKDIR /app

# gcc is required by some Python C extensions during pip install
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY templates/ templates/
COPY static/ static/
COPY scripts/ scripts/
COPY .env.example .env.example

RUN mkdir -p data logs

ENV PYTHONPATH=/app/src \
    FINBRIEF_DB=/app/data/finbrief.db

EXPOSE 8780

CMD ["uvicorn", "finbrief.app:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8780"]
