# Build from the SUD_YOLO26 root using the allowlisted .dockerignore.
ARG SCIENTIFIC_BASE=scientific_dependencies
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 \
    PYTHONPATH=/opt/sud/Web_app/backend SUD_DATA_DIR=/data HOME=/tmp
RUN groupadd --gid 10001 sud && useradd --uid 10001 --gid sud --no-create-home sud
WORKDIR /opt/sud
COPY Web_app/requirements.txt /tmp/web-requirements.txt
RUN pip install --no-cache-dir -r /tmp/web-requirements.txt

FROM runtime AS api
COPY Web_app/backend Web_app/backend
COPY Web_app/static Web_app/static
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "sudweb.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--no-proxy-headers"]

FROM runtime AS scientific_dependencies
USER root
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 install -y --no-install-recommends \
    libgl1 libegl1 libglib2.0-0 libxkbcommon0 libdbus-1-3 libxcb-cursor0 libxrender1 libsm6 libxext6 libice6 \
    libfreetype6 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*
COPY Application/requirements.txt Application/constraints.txt /tmp/
RUN pip install --no-cache-dir torch==2.7.1 torchvision==0.22.1 --index-url ${TORCH_INDEX_URL} \
    && pip install --no-cache-dir -r /tmp/requirements.txt -c /tmp/constraints.txt \
    && pip install --no-cache-dir "pylibjpeg>=2,<3" "pylibjpeg-libjpeg>=2,<3" "pylibjpeg-openjpeg>=2,<3" "pyjpegls>=1.5,<2"

# Code updates do not invalidate the scientific dependency stage.
# SCIENTIFIC_BASE may point at a trusted prebuilt runtime with the same dependencies.
FROM ${SCIENTIFIC_BASE} AS worker
USER root
RUN python -c "import ctypes; ctypes.CDLL('libfreetype.so.6'); ctypes.CDLL('libfontconfig.so.1')" \
    || (apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 install -y --no-install-recommends libfreetype6 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*)
COPY Web_app/backend Web_app/backend
COPY Web_app/static Web_app/static
COPY Application/src Application/src
COPY Application/config/detection.yaml Application/config/analysis.yaml Application/config/
ENV MPLBACKEND=Agg QT_QPA_PLATFORM=offscreen OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 SUD_DEVICE=cpu SUD_WEIGHTS=/models/kidney_yolo26.pt
RUN PYTHONPATH=/opt/sud/Application/src:/opt/sud/Web_app/backend python -c "from sud_yolo26.analysis import parameters; import pyvista; import pydicom"
USER 10001:10001
CMD ["python", "-m", "sudweb.worker"]
