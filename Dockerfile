FROM python:3.12-slim

RUN apt-get update && apt-get install -y tzdata cron python3-elasticsearch

WORKDIR /scripts
COPY requirements.txt /scripts
RUN pip3 install --no-cache-dir -r requirements.txt

ENV TZ=Europe/Madrid
ENV PYTHONUNBUFFERED=1
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# crontab.txt is volume-mounted to /etc/cron.d/mycron via docker-compose
# chmod is applied at runtime by entrypoint.sh
RUN touch /var/log/cron.log

# Keep container alive and show cron logs
#CMD cron && tail -f /var/log/cron.log

COPY entrypoint.sh /
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]