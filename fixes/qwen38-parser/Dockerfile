# Preserve the incident image's model support and kernels; backport only #52830.
FROM vllm/vllm-openai@sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8
COPY apply_fix.py /opt/qwen38-parser-fix/apply_fix.py
RUN python3 -B /opt/qwen38-parser-fix/apply_fix.py --apply
