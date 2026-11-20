FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Estáticos del admin (los sirve WhiteNoise). La clave es solo para este paso
RUN DJANGO_SECRET_KEY=solo-para-collectstatic python manage.py collectstatic --noinput

# Cloud Run indica el puerto en $PORT (8080 por defecto)
CMD exec gunicorn config.wsgi:application --bind :${PORT:-8080} --workers 2 --threads 4 --timeout 0
