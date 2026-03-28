FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire application
COPY . .

# Create data directory for SQLite
RUN mkdir -p /app/data /app/data/backups

# Fix line endings (Windows CRLF -> Linux LF) and make entrypoint executable
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Expose the port Streamlit runs on (HF Spaces expects 7860)
EXPOSE 7860

# Use entrypoint script that handles persistent storage
ENTRYPOINT ["/app/entrypoint.sh"]
