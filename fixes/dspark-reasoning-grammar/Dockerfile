# Use the inspected DSpark image's immutable digest or local image ID.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY apply_fix.py patch.json /opt/dspark-reasoning-grammar-fix/
RUN python3 -B /opt/dspark-reasoning-grammar-fix/apply_fix.py --apply
