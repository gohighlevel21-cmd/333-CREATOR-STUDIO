FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir higgsfield-client==0.2.0 'psycopg[binary]>=3.2,<4' 'python-dotenv>=1.0,<2.0' && useradd --system studio
COPY src/ src/
COPY creator_studio_bridge.py .
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1
USER studio
CMD ["python", "creator_studio_bridge.py"]
