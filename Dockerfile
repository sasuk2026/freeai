FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway.py index.html catalog.json catalog_web.json ./
ENV PORT=8000
EXPOSE 8000
# shell 形式，确保 $PORT 被展开（Render 会注入 PORT）
CMD gunicorn -w 2 -k gthread --threads 8 --timeout 120 -b 0.0.0.0:$PORT gateway:app
