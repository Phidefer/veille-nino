# Option pour reconstruire l'image sur une machine ayant acces a Internet.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 enso \
    && useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home --shell /usr/sbin/nologin enso
COPY index.html server.py refresh_sources.py collect_sector.py history.py ./
COPY data/state.json data/feeds.json ./data/
RUN chown -R 10001:10001 /app/data
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "/app/server.py"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--auto-hours", "12", "--sector-hours", "24"]
