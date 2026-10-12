FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd -r -u 10002 mandarin && mkdir -p /app/data && chown mandarin /app/data
USER mandarin
CMD ["python", "-m", "app.main"]
