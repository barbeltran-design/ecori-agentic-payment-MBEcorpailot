FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ecori_payment_agent.py .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "ecori_payment_agent.py"]
