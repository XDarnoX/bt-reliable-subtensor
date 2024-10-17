FROM python:3.11-slim

WORKDIR /usr/src/app

COPY pyproject.toml .
COPY pdm.lock .

RUN apt-get update && apt-get install -y iptables
RUN python3 -m pip install --no-cache-dir 'pdm>=2.12,<3'

RUN pdm config python.use_venv False && \
    pdm lock --check && \
    pdm sync --prod --group :all
RUN mkdir -p /opt/ && mv __pypackages__/3.11/ /opt/pypackages/
ENV PATH=/opt/pypackages/bin:$PATH
ENV PYTHONPATH=/opt/pypackages/lib:$PYTHONPATH

COPY . .
RUN chmod +x ./entrypoint.sh

CMD [ "./entrypoint.sh" ]
