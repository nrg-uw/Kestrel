FROM python:3.10-slim
WORKDIR /kestrel
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p output
ENTRYPOINT ["python", "kestrel.py"]
