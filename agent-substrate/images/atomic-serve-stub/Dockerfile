# SPDX-License-Identifier: Apache-2.0
#
# A stand-in for atomic serve: records what reaches it. Goes when atomic's own
# image serves the gate endpoint.
#
#   docker build -t localhost:5000/atomic-serve-stub:<tag> images/atomic-serve-stub

# python:3.13-slim-trixie
FROM python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
COPY stub.py /opt/atomic/stub.py
USER 65532
EXPOSE 8080
CMD ["python3", "-I", "-u", "/opt/atomic/stub.py"]
