# Supply the inspected Brain-12 base image by immutable digest/image ID.
# No default: an unrelated vLLM image must not replace DSpark/vision kernels.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY apply_fix.py /opt/dspark-required-parser-fix/apply_fix.py
RUN python3 -B /opt/dspark-required-parser-fix/apply_fix.py --apply
