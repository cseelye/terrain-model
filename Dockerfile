FROM ubuntu:16.04
WORKDIR /root/
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install --assume-yes \
        curl \
        python \
        python-dev \
        software-properties-common && \
    curl https://bootstrap.pypa.io/get-pip.py | python
COPY requirements.txt /tmp/
RUN add-apt-repository -y ppa:ubuntugis/ppa && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install --assume-yes \
        gdal-bin \
        libgdal-dev \
        python-affine \
        python-gdal && \
    pip install -r /tmp/requirements.txt && \
    apt-get autoremove --assume-yes && \
    apt-get clean && \
    rm --force --recursive /var/lib/apt/lists/* /tmp/* /var/tmp/*
COPY . /root/
