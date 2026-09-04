FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -e ".[test]"
COPY backend/app ./app
EXPOSE 8688
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8688"]
