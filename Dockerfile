FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# =========================================================
# System converters
# =========================================================

RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice \
    libreoffice-draw \
    dcraw \
    imagemagick \
    fonts-liberation \
    fonts-dejavu \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# =========================================================
# Non-root application user
# =========================================================

RUN useradd \
    --create-home \
    --shell /usr/sbin/nologin \
    appuser

WORKDIR /app

# =========================================================
# Python dependencies
# =========================================================

RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    python-multipart

# =========================================================
# Application
# =========================================================

COPY app.py /app/app.py

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 7860

# =========================================================
# Start API
# =========================================================

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]