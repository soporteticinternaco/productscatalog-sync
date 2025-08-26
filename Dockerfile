FROM python:3.12-slim

RUN apt-get update && apt-get install -y tzdata cron python3-elasticsearch

WORKDIR /scripts
COPY requirements.txt /scripts
RUN pip3 install --no-cache-dir -r requirements.txt

ENV TZ=Europe/Madrid
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Copy your crontab file into the container
#COPY crontab.txt /etc/cron.d/mycron
#RUN chmod 0644 /etc/cron.d/mycron

# Apply cron file and create a log file
#RUN crontab /etc/cron.d/mycron
RUN touch /var/log/cron.log

# Keep container alive and show cron logs
CMD cron && tail -f /var/log/cron.log

