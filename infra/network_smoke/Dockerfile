FROM python:3.12.11-alpine3.22@sha256:efcdfa6a6b2fd2afb9c7dfa9a5b288a6f68338b5cfdebe6b637d986067d85757

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /probe
COPY probe.py /probe/probe.py

USER 65532:65532
ENTRYPOINT ["python", "/probe/probe.py"]
