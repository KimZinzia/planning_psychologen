FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY src/ src/
COPY ui/ ui/
COPY assets/ assets/
COPY .streamlit/ .streamlit/

# De database staat in /app/data; koppel daar een volume aan zodat de
# gegevens een herstart overleven.
ENV PLANNING_DB=/app/data/planning.db
VOLUME /app/data

EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
