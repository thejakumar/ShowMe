# ShowMe web server (and, with a different command, a job worker). See DEPLOY.md.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    APPRENTICE_DATA=/data APPRENTICE_TRACES=/data/traces
WORKDIR /app

COPY requirements.txt requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-server.txt

COPY backend ./backend
COPY manuals ./manuals
RUN useradd --create-home showme && mkdir -p /data && chown showme /data
# Starts as root only to hand a mounted disk at /data to 'showme', then runs as 'showme' (backend/start.py).

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % __import__('os').environ.get('PORT', '8000'), timeout=4).status == 200 else 1)"

# Web: several processes per container, behind a load balancer that terminates HTTPS.
ENTRYPOINT ["python", "backend/start.py"]
