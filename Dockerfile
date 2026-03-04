# Imagen base
FROM python:3.10-slim

# Directorio de trabajo
WORKDIR /app

# Configuración de entorno
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=America/Caracas

# Instalar dependencias de sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Instalar librerías de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el script
COPY estrategia_crt_v2.py .

COPY usuarios.txt .

# Ejecutar
CMD python estrategia_crt_v2.py & python bot_mora_trader.py