# Container for the Codebook Studio UI, deployed to AWS App Runner.
# The UI reaches Aurora (Data API) and Step Functions over public AWS endpoints using the
# App Runner instance role, so no reference data is baked into the image.
FROM python:3.12-slim

WORKDIR /app
COPY requirements-app-runner.txt .
RUN pip install --no-cache-dir -r requirements-app-runner.txt

COPY src ./src
ENV PYTHONPATH=/app/src
ENV STREAMLIT_SERVER_HEADLESS=true

EXPOSE 8501
CMD ["streamlit", "run", "src/ambit_codebook/review_app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false"]
