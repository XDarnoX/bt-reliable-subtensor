FROM python:3.11-slim

WORKDIR /usr/src/app

COPY pyproject.toml .
COPY pdm.lock .
RUN apt-get update && apt-get install -y iptables 
RUN python3 -m pip install --no-cache-dir 'pdm>=2.12,<3'

RUN pdm config python.use_venv False && \
    pdm lock --check && \
    pdm sync --prod
RUN mkdir -p /opt/ && mv __pypackages__/3.11/ /opt/pypackages/
ENV PATH=/opt/pypackages/bin:$PATH
ENV PYTHONPATH=/usr/src/app/src:/opt/pypackages/lib:$PYTHONPATH

COPY . .
RUN chmod +x ./subtensor_monitor_launcher.py

CMD [ "python", "subtensor_monitor_launcher.py" ]
