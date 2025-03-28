# Build stage
FROM python:3.13-alpine AS builder

RUN apk add --no-cache \
    gcc \
    musl-dev \
    curl \
    unzip

# Install poetry
RUN pip install poetry

# Set working directory
WORKDIR /app

# Copy the project files
COPY . .

# Install dependencies using poetry
RUN poetry config virtualenvs.create false && \
    poetry install --only main --no-interaction --no-ansi

# Final stage
FROM python:3.13-alpine

WORKDIR /app

# Copy only the necessary files from builder
COPY --from=builder /app /app
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

# Create a directory for downloads
RUN mkdir /downloads

# Set the entrypoint
ENTRYPOINT ["python", "bandcamp-downloader.py"]

# Set default command (can be overridden)
CMD ["--help"]