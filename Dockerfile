FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

RUN pip install --upgrade pip && pip install .

CMD ["sh", "-c", "uvicorn zira_dashboard.app:app --host 0.0.0.0 --port ${PORT}"]
