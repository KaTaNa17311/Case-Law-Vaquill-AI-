FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend /app/backend
COPY frontend /app/frontend
RUN useradd --uid 10001 --create-home researcher && mkdir /data && chown researcher /data
RUN chmod -R a+rX /app/backend /app/frontend
USER researcher
EXPOSE 8000
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
