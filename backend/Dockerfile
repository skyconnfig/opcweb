FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -e ".[test]"
COPY alembic.ini ./
COPY backend/alembic ./backend/alembic
COPY backend/app ./app
EXPOSE 8689
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8689"]
