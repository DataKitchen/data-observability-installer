FROM python:3.12-slim
LABEL maintainer="DataKitchen"

WORKDIR /dk
COPY deploy/requirements.txt .
RUN pip install -r requirements.txt

COPY demo/* .
COPY input_data input_data

ENV PYTHONUNBUFFERED=1

ENTRYPOINT [ "python", "main.py" ]
