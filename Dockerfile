# Use a lightweight Python image
FROM python:3.12-slim

# Set the working directory
WORKDIR /app

# Install system dependencies (if needed for libraries like requests)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Kopf and the Kubernetes client
# Adding 'requests' since you'll likely use it for your external API
COPY . .

RUN pip install . --no-cache-dir

# Run Kopf when the container starts
# --all-namespaces allows the operator to watch the whole cluster
# or remove it to restrict to the namespace it's deployed in
CMD ["kopf", "run", "kalavai_job_operator/job_operator.py", "--all-namespaces"]