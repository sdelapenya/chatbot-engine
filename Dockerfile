# Imagen del motor. No sustituye a lo que corre en el servidor (una unidad de
# systemd por instancia, uvicorn sobre el mismo código): es la forma de levantar
# una instancia en cualquier máquina sin instalar Python ni dependencias.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LOG_DIR=/app/data \
    FRONTEND_DIR=/app/frontend \
    PORT=8000

WORKDIR /app

# Las dependencias primero: tocar un .py no invalida la capa de pip.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Sin privilegios. Solo necesita leer el código y escribir en data/ (leads,
# conversaciones y sesiones), que en producción se monta como volumen.
RUN useradd --create-home --uid 10001 chatbot \
    && mkdir -p /app/data \
    && chown -R chatbot:chatbot /app
USER chatbot

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=4)"

WORKDIR /app/backend

# Un worker: las sesiones activas viven en memoria del proceso y con dos
# workers una conversación saltaría entre estados distintos. El tráfico de una
# instancia (un chat en una web) le sobra de largo.
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
