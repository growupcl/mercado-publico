FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LICITA_DIR_DOCUMENTOS=/datos/documentos

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home calza && mkdir -p /datos/documentos && chown -R calza /datos
USER calza

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/salud')" || exit 1
CMD ["licita", "servidor", "--host", "0.0.0.0", "--puerto", "8000"]
