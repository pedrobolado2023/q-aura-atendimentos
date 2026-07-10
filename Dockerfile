FROM python:3.10-slim

WORKDIR /app

# Evita que o Python gere arquivos .pyc
ENV PYTHONDONTWRITEBYTECODE 1
# Mantém os logs em tempo real
ENV PYTHONUNBUFFERED 1

# Instala dependências do requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o projeto (incluindo a pasta frontend/)
COPY . .

# Expõe a porta que o FastAPI usa
EXPOSE 8000

# Executa o uvicorn apontando para o app.main
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
