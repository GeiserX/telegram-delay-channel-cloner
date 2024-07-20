FROM python:3.12-alpine 

# USER root

# RUN apt-get update && \
#     apt-get install -y libxml2-dev libxslt-dev build-essential libssl-dev libffi-dev && \
#     apt-get clean

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .
CMD ["python3", "-u", "src/main.py"]