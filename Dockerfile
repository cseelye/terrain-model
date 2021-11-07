ARG GDAL_VERSION=3.3.2


FROM ubuntu:20.04 as gdal_build

RUN printf '\
APT::Install-Recommends "0";\n\
APT::Install-Suggests "0";\n\
' >> /etc/apt/apt.conf.d/01norecommends
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get udpate && \
    apt-get install --yes \
    g++-9-x86-64-linux-gnu








# Build stage
FROM osgeo/gdal:ubuntu-small-${GDAL_VERSION} as build
ARG BLENDER_BRANCH="blender-v2.93-release"
ARG CORES=8
ARG BLENDER_WORK_DIR=/blender-build

# Install build tools and other required tools/libraries
RUN printf '\
APT::Install-Recommends "0";\n\
APT::Install-Suggests "0";\n\
' >> /etc/apt/apt.conf.d/01norecommends
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
        build-essential \
        git \
        subversion \
        cmake \
        libx11-dev \
        libxxf86vm-dev \
        libxcursor-dev \
        libxi-dev \
        libxrandr-dev \
        libxinerama-dev \
        libglew-dev \
        ca-certificates \
        sudo \
        wget \
        python3-dev \
        cmake \
        gnupg \
        python-is-python3 \
    && \
    curl https://bootstrap.pypa.io/get-pip.py | python3 && \
    git config --global user.email "buildscript@blender-build.invalid" && git config --global user.name "Build Script" && \
    apt-key adv --keyserver keyserver.ubuntu.com --recv-keys F23C5A6CF475977595C89F51BA6932366A755776 && \
    echo "deb http://ppa.launchpad.net/deadsnakes/ppa/ubuntu focal main" > /etc/apt/sources.list.d/deadsnakes.list && \
    apt-get update && \
    apt-get install --yes --no-install-recommends python3.9 && \
    curl https://bootstrap.pypa.io/get-pip.py | python3.9 && \
    apt-get autoremove --yes && apt-get clean && rm -rf /var/lib/apt/lists/*

# To speed up debugging/iteration on the container image, checkout the blender
# source in your build context and uncomment the COPY line to copy it into the
# container instead of checking it out every time.
#   mkdir -p buildfiles/blender-build/lib
#   git clone --depth 1 --branch blender-v2.93-release --single-branch https://git.blender.org/blender.git buildfiles/blender-build/blender-src
#   svn --non-interactive checkout https://svn.blender.org/svnroot/bf-blender/tags/blender-2.93-release/lib/linux_centos7_x86_64 buildfiles/blender-build/lib/linux_centos7_x86_64
#COPY buildfiles/ /

# Get the blender source and build/install the bpy python module.
# This creates a "user" install which is easy to copy from /root/.local
COPY buildfiles/build-bpy /
RUN /build-bpy

# Install other python modules into python
# This creates a "user" install which is easy to copy from /root/.local
COPY requirements.txt /tmp/
RUN mkdir /python-modules && \
    python3 -m pip --no-cache-dir install --upgrade --requirement=/tmp/requirements.txt --user && \
    python3.9 -m pip --no-cache-dir install --upgrade --requirement=/tmp/requirements.txt --user && \
    rm --force /tmp/requirements.txt


# Production image stage
FROM osgeo/gdal:ubuntu-small-${GDAL_VERSION} as prod
COPY --from=build /etc/apt/apt.conf.d/01norecommends /etc/apt/apt.conf.d/
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install --yes \
        gnupg \
        libx11-6 \
        libxxf86vm1 \
        libxfixes3 \
        libxrender1 \
        libgl1 \
        libgomp1 \
        openscad \
    && \
    apt-key adv --keyserver keyserver.ubuntu.com --recv-keys F23C5A6CF475977595C89F51BA6932366A755776 && \
    echo "deb http://ppa.launchpad.net/deadsnakes/ppa/ubuntu focal main" > /etc/apt/sources.list.d/deadsnakes.list && \
    apt-get update && \
    apt-get install --yes python3.9 && \
    echo "/root/.local" > /usr/local/lib/python3.9/dist-packages/site-packages.pth && \
    apt-get autoremove --yes && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy the python modules installed in the build stage
COPY --from=build /root/.local /root/.local

# Dev image stage - adds tools useful for development but not necessary for
# runtime
FROM prod as dev
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install --yes \
        git \
        python3-distutils \
        python3.9-distutils \
        vim \
        && \
    apt-get autoremove --yes && apt-get clean && rm -rf /var/lib/apt/lists/* && \
    curl https://bootstrap.pypa.io/get-pip.py | python3 && \
    curl https://bootstrap.pypa.io/get-pip.py | python3.9
COPY requirements*.txt /tmp/
RUN python3 -m pip install --no-cache-dir --upgrade --user --requirement /tmp/requirements-dev.txt && \
    python3.9 -m pip install --no-cache-dir --upgrade --user --requirement /tmp/requirements-dev.txt && \
    rm --force /tmp/requirements*.txt
