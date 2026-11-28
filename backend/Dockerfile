FROM python:3.11-slim
WORKDIR /app
RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir fastapi uvicorn redis "psycopg[binary]" pandas openpyxl python-multipart
COPY . .
CMD sh -c "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2"
