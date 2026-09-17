# Diagnostic image only. No RUN, application, cloud CLI, gh, secrets or workflow.
# Exact linux/amd64 child of the already-used multi-platform Python base pin.
FROM python:3.12.11-alpine3.22@sha256:5e511b0e940def74dd23389510300c76017ca9bbc92484c40cff0951adf32245

COPY scripts/staging_runtime_capability_probe.py /probe/staging_runtime_capability_probe.py
COPY scripts/staging_offline_verifier_diagnostics.py /probe/staging_offline_verifier_diagnostics.py
WORKDIR /probe
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/python3", "-I", "-S", "-B", "/probe/staging_runtime_capability_probe.py"]
CMD []
