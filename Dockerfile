# GDAL version to use
ARG GDAL_VERSION="3.5.0"
# Blender version to use
ARG BLENDER_BRANCH="blender-v3.1-release"
# Python version to use - this must match what blender is expecting
ARG PYTHON_VERSION="3.10.2"
ARG PYTHON_VERSION_SHORT="3.10"
# Number CPU cores to limit to while building
ARG CORES=4


# Internal variables used across stages
ARG PROJ_INSTALL_PREFIX=/usr/local
ARG GDAL_DEST=/build_gdal
ARG PYTHON_DEST=/build_python3
ARG BPY_DEST=/opt/bpy/site-packages
ARG BLENDER_DEST=/opt/blender
ARG GCC_ARCH=x86_64


#
# Primordial base stage
# Shared base layers for all other stages
#
FROM ubuntu:20.04 AS primordial
# Bring in build args
ARG BLENDER_BRANCH
ARG BLENDER_DEST
ARG BPY_DEST
ARG CORES
ARG GCC_ARCH
ARG GDAL_DEST
ARG GDAL_VERSION
ARG PROJ_INSTALL_PREFIX
ARG PYTHON_DEST
ARG PYTHON_VERSION
ARG PYTHON_VERSION_SHORT

SHELL ["/bin/bash", "-xo", "pipefail", "-c"]

# Add .local to the path for pip installed tools
ENV PATH="${PATH}:/root/.local/bin"

# Configure apt to never install recommended packages and do not prompt for user input
RUN printf 'APT::Install-Recommends "0";\nAPT::Install-Suggests "0";\n' >> /etc/apt/apt.conf.d/01norecommends
ENV DEBIAN_FRONTEND=noninteractive

# Set locale and timezone
RUN ln -fs /usr/share/zoneinfo/Etc/UTC /etc/localtime && \
    apt-get update && \
    apt-get install --yes \
        locales=2.31-0ubuntu9.9 \
        tzdata=2022a-0ubuntu0.20.04 \
    && \
    apt-get autoremove --yes && apt-get clean && rm -rf /var/lib/apt/lists/* && \
    locale-gen "en_US.UTF-8"
ENV LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8
# Install common packages and python dependencies
RUN apt-get update && \
    apt-get install --yes \
        ca-certificates \
        curl=7.68.0-1ubuntu2.12 \
        gnupg=2.2.19-3ubuntu2.2 \
        libreadline5=5.2+dfsg-3build3 \
        libncurses6=6.2-0ubuntu2 \
        libssl1.1=1.1.1f-1ubuntu2.16 \
        libsqlite3-0=3.31.1-4ubuntu0.3 \
        libtk8.6=8.6.10-1 \
        libgdbm6=1.18.1-5 \
        libbz2-1.0=1.0.8-2 \
        libffi7=3.3-4 \
    && \
    apt-get autoremove --yes && apt-get clean && rm -rf /var/lib/apt/lists/*

#
# Python build stage
#
# Here we build python from source instead of installing from a repo so that
# we can get the exact version we want without dealing with the distro
# default python and custom packaging. Eg debian distributes a specific
# version of python3, splits it into multiple packages, and makes it
# difficult to install other versions instead.
# Blender only supports very specific python versions, and GDAL must be built
# for the python in use, so to avoid having multiple pythons to manage for
# the distro default, blender, and GDAL, instead build the one version we want.
FROM primordial AS build_python
# Bring in build args
ARG BLENDER_BRANCH
ARG BLENDER_DEST
ARG BPY_DEST
ARG CORES
ARG GCC_ARCH
ARG GDAL_DEST
ARG GDAL_VERSION
ARG PROJ_INSTALL_PREFIX
ARG PYTHON_DEST
ARG PYTHON_VERSION
ARG PYTHON_VERSION_SHORT

RUN apt-get update && \
    apt-get install --yes \
        build-essential=12.8ubuntu1.1 \
        checkinstall=1.6.2+git20170426.d24a630-2ubuntu1 \
        libreadline-gplv2-dev=5.2+dfsg-3build3 \
        libncursesw5-dev=6.2-0ubuntu2 \
        libssl-dev=1.1.1f-1ubuntu2.16 \
        libsqlite3-dev=3.31.1-4ubuntu0.3 \
        tk-dev=8.6.9+1 \
        libgdbm-dev=1.18.1-5 \
        libc6-dev=2.31-0ubuntu9.9 \
        libbz2-dev=1.0.8-2 \
        libffi-dev=3.3-4 \
        liblzma-dev=5.2.4-1ubuntu1.1 \
        libgdbm-compat-dev=1.18.1-5
RUN curl -LSsf https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz | tar -xz && \
    cd Python-${PYTHON_VERSION} && \
    ./configure --prefix=${PYTHON_DEST} && \
    make -j${CORES} EXTRA_CFLAGS='-g0 -Os' LDFLAGS='-s' && \
    make install
RUN ${PYTHON_DEST}/bin/python3 -m ensurepip --upgrade --default-pip && \
    ${PYTHON_DEST}/bin/python3 -m pip install \
        --no-cache-dir \
        --upgrade \
        --compile \
        pip wheel
# Fix the shebangs for when we relocate this python install to /usr
RUN for f in $(find ${PYTHON_DEST}/bin/ -type f -exec file {} \; | grep "Python script" | cut -d: -f1); do sed "s|${PYTHON_DEST}|/usr|" -i ${f}; done

#
# Base stage
# Shared layers all other stages share (primordial layers plus python runtime)
#
FROM primordial AS base
# Bring in build args
ARG BLENDER_BRANCH
ARG BLENDER_DEST
ARG BPY_DEST
ARG CORES
ARG GCC_ARCH
ARG GDAL_DEST
ARG GDAL_VERSION
ARG PROJ_INSTALL_PREFIX
ARG PYTHON_DEST
ARG PYTHON_VERSION
ARG PYTHON_VERSION_SHORT

COPY --from=build_python ${PYTHON_DEST} /usr/
RUN ldconfig
# Do not warn about running pip as root
ENV PIP_ROOT_USER_ACTION=ignore


#
# Python modules build stage
#
# This will install/build python modules as a "user" install which is easy to copy from /root/.local to other layers
FROM base AS build_py_modules
# Bring in build args
ARG BLENDER_BRANCH
ARG BLENDER_DEST
ARG BPY_DEST
ARG CORES
ARG GCC_ARCH
ARG GDAL_DEST
ARG GDAL_VERSION
ARG PROJ_INSTALL_PREFIX
ARG PYTHON_DEST
ARG PYTHON_VERSION
ARG PYTHON_VERSION_SHORT

RUN apt-get update && \
    apt-get install --yes build-essential
COPY requirements.txt /tmp/
# Some inspiration from https://towardsdatascience.com/how-to-shrink-numpy-scipy-pandas-and-matplotlib-for-your-data-product-4ec8d7e86ee4
RUN CFLAGS='-g0 -Os' LD_FLAGS='-s' pip3 -v install \
        --no-cache-dir \
        --compile \
        --user \
        $(grep numpy /tmp/requirements.txt | head -n 1) \
        --global-option="build_ext" \
        --global-option="-j ${CORES}"
RUN pip3 install \
        --no-cache-dir \
        --upgrade \
        --compile \
        --user \
        --requirement=/tmp/requirements.txt \
    && \
    rm --force /tmp/requirements.txt


#
# GDAL build stage
#
FROM base AS build_gdal
# Bring in build args
ARG BLENDER_BRANCH
ARG BLENDER_DEST
ARG BPY_DEST
ARG CORES
ARG GCC_ARCH
ARG GDAL_DEST
ARG GDAL_VERSION
ARG PROJ_INSTALL_PREFIX
ARG PYTHON_DEST
ARG PYTHON_VERSION
ARG PYTHON_VERSION_SHORT

# Install build tools
RUN apt-get update && \
    apt-get install --yes \
        autoconf=2.69-11.1 \
        automake=1:1.16.1-4ubuntu6 \
        bash=5.0-6ubuntu1.2 \
        build-essential=12.8ubuntu1.1 \
        ca-certificates \
        cmake=3.16.3-1ubuntu1 \
        curl=7.68.0-1ubuntu2.12 \
        git=1:2.25.1-1ubuntu3.5 \
        gnupg=2.2.19-3ubuntu2.2 \
        libtool=2.4.6-14 \
        make=4.2.1-1.2 \
        pkg-config=0.29.1-0ubuntu4 \
        unzip=6.0-25ubuntu1

# PROJ dependencies
RUN apt-get install --yes \
        libcurl4-gnutls-dev \
        libsqlite3-dev \
        libtiff5-dev \
        sqlite3 \
        zlib1g-dev

# GDAL dependencies
RUN apt-get install --yes \
        libexpat1-dev=2.2.9-1ubuntu0.4 \
        libgeos-3.8.0=3.8.0-1build1 \
        libjpeg-dev=8c-2ubuntu8 \
        libopenjp2-7-dev=2.3.1-1ubuntu4.20.04.1 \
        libpng-dev=1.6.37-2 \
        libpq-dev=12.11-0ubuntu0.20.04.1 \
        libssl-dev=1.1.1f-1ubuntu2.16 \
        libwebp-dev=0.6.1-2ubuntu0.20.04.1 \
        libxerces-c-dev=3.2.2+debian-1build3 \
        libzstd-dev=1.4.4+dfsg-3ubuntu0.1 \
        sqlite3=3.31.1-4ubuntu0.3

# Get numpy so that gdal_array support gets built
COPY --from=build_py_modules /root/.local /usr/

# Get the source and build PROJ and GDAL
COPY container_build/build-proj container_build/build-gdal /
RUN /build-proj
RUN /build-gdal


#
# Blender build stage
#
FROM base AS build_blender
# Bring in build args
ARG BLENDER_BRANCH
ARG BLENDER_DEST
ARG BPY_DEST
ARG CORES
ARG GCC_ARCH
ARG GDAL_DEST
ARG GDAL_VERSION
ARG PROJ_INSTALL_PREFIX
ARG PYTHON_DEST
ARG PYTHON_VERSION
ARG PYTHON_VERSION_SHORT

# Install build tools and other required tools/libraries
RUN apt-get update && \
    apt-get install --yes \
        build-essential=12.8ubuntu1.1 \
        cmake=3.16.3-1ubuntu1 \
        git=1:2.25.1-1ubuntu3.5 \
        libx11-dev=2:1.6.9-2ubuntu1.2 \
        libxxf86vm-dev=1:1.1.4-1build1 \
        libxcursor-dev=1:1.2.0-2 \
        libxi-dev=2:1.7.10-0ubuntu1 \
        libxrandr-dev=2:1.5.2-0ubuntu1 \
        libxinerama-dev=2:1.1.4-2 \
        libglew-dev=2.1.0-4 \
        opencollada-dev=0.1.0~20180719.619d942+dfsg0-2build1 \
        subversion=1.13.0-3ubuntu0.2 \
        sudo=1.8.31-1ubuntu1.2 \
        wget=1.20.3-1ubuntu2 \
        xz-utils=5.2.4-1ubuntu1.1 \
    && \
    apt-get autoremove --yes && apt-get clean && rm -rf /var/lib/apt/lists/* && \
    git config --global user.email "buildscript@blender-build.invalid" && git config --global user.name "Build Script"

# To speed up debugging/iteration on the container image, checkout the blender
# source in your build context and uncomment the COPY line to copy it into the
# container instead of checking it out every time.
#   mkdir -p container_build/blender-build/lib
#   git clone --depth 1 --branch blender-v3.1-release --single-branch https://git.blender.org/blender.git container_build/blender-build/blender-src
#   pushd container_build/blender-build/blender-src; git submodule update --init --recursive; popd
#   svn --non-interactive checkout https://svn.blender.org/svnroot/bf-blender/tags/blender-3.1-release/lib/linux_centos7_x86_64 container_build/blender-build/lib/linux_centos7_x86_64
#COPY container_build/ /

# Get the blender source and build/install the bpy python module and blender app.
# This creates a "user" install of the python modules which is easy to copy from /root/.local
COPY container_build/build-bpy /
RUN /build-bpy

# Install python modules into blender's python
COPY requirements.txt /tmp/requirements.txt
RUN ${BLENDER_DEST}/*/python/bin/python* -m ensurepip && \
    ${BLENDER_DEST}/*/python/bin/python* -m pip install \
        --disable-pip-version-check \
        --no-cache-dir \
        --upgrade \
            pip wheel && \
    ${BLENDER_DEST}/*/python/bin/python* -m pip install \
        --no-cache-dir \
        --upgrade \
        --requirement /tmp/requirements.txt


#
# Production image stage
#
FROM base AS prod
LABEL org.opencontainers.image.source=https://github.com/cseelye/terrain-model
LABEL org.opencontainers.image.licenses=MIT
LABEL org.opencontainers.image.description="terrain-model runtime container"
# Bring in build args
ARG BLENDER_BRANCH
ARG BLENDER_DEST
ARG BPY_DEST
ARG CORES
ARG GCC_ARCH
ARG GDAL_DEST
ARG GDAL_VERSION
ARG PROJ_INSTALL_PREFIX
ARG PYTHON_DEST
ARG PYTHON_VERSION
ARG PYTHON_VERSION_SHORT

# Install runtime dependencies for PROJ and GDAL
RUN apt-get update && \
    apt-get install --yes \
        libcurl4=7.68.0-1ubuntu2.12 \
        libexpat1=2.2.9-1ubuntu0.4 \
        libgeos-3.8.0=3.8.0-1build1 \
        libgeos-c1v5=3.8.0-1build1 \
        libjpeg-turbo8=2.0.3-0ubuntu1.20.04.1 \
        libopenjp2-7=2.3.1-1ubuntu4.20.04.1 \
        libpng16-16=1.6.37-2 \
        libpq5=12.11-0ubuntu0.20.04.1 \
        libsqlite3-0=3.31.1-4ubuntu0.3 \
        libssl1.1=1.1.1f-1ubuntu2.16 \
        libtiff5=4.1.0+git191117-2ubuntu0.20.04.3 \
        libwebp6=0.6.1-2ubuntu0.20.04.1 \
        libxerces-c3.2=3.2.2+debian-1build3 \
        libzstd1=1.4.4+dfsg-3ubuntu0.1 \
    && \
    apt-get autoremove --yes && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install runtime dependencies for blender
RUN apt-get update && \
    apt-get install --yes \
        libx11-6=2:1.6.9-2ubuntu1.2 \
        libxxf86vm1=1:1.1.4-1build1 \
        libxfixes3=1:5.0.3-2 \
        libxrender1=1:0.9.10-1 \
        libgl1=1.3.2-1~ubuntu0.20.04.2 \
        libgomp1=10.3.0-1ubuntu1~20.04 \
        libxi6=2:1.7.10-0ubuntu1 \
        opencollada-dev=0.1.0~20180719.619d942+dfsg0-2build1 \
        openscad=2019.05-3ubuntu5 \
        # openbox=3.6.1-9ubuntu0.20.04.1 \
        # xorg=1:7.7+19ubuntu14 \
        xvfb=2:1.20.13-1ubuntu1~20.04.3 \
    && \
    apt-get autoremove --yes && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy the PROJ/GDAL modules from the gdal build stage
COPY --from=build_gdal ${GDAL_DEST} /

# Copy the python modules from the blender build stage
COPY --from=build_blender ${BPY_DEST} /usr/lib/python${PYTHON_VERSION_SHORT}/site-packages

# Copy blender app from the blender build stage
COPY --from=build_blender ${BLENDER_DEST} /opt/blender

# Copy the python modules from the python module build stage
COPY --from=build_py_modules /root/.local /usr/

# Run ldconfig to make sure shared libraries are configured correctly
RUN ldconfig

ENV DISPLAY=:0
ENV PATH="${PATH}:/opt/blender:/opt/terrain-model"

# Copy the scripts into /opt/terrain-model
COPY *.py gtm* LICENSE /opt/terrain-model/


#
# Dev image stage - adds tools useful for development but not necessary for
# runtime
#
FROM prod AS dev
LABEL org.opencontainers.image.source=https://github.com/cseelye/terrain-model
LABEL org.opencontainers.image.licenses=MIT
LABEL org.opencontainers.image.description="terrain-model development container"
# Bring in build args
ARG BLENDER_BRANCH
ARG BLENDER_DEST
ARG BPY_DEST
ARG CORES
ARG GCC_ARCH
ARG GDAL_DEST
ARG GDAL_VERSION
ARG PROJ_INSTALL_PREFIX
ARG PYTHON_DEST
ARG PYTHON_VERSION
ARG PYTHON_VERSION_SHORT

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install --yes \
        ack=3.3.1-1 \
        build-essential=12.8ubuntu1.1 \
        git=1:2.25.1-1ubuntu3.5 \
        tree=1.8.0-1 \
        vim=2:8.1.2269-1ubuntu5.7 \
        && \
    apt-get autoremove --yes && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY requirements*.txt /tmp/
RUN pip3 install \
        --no-cache-dir \
        --upgrade \
        --compile \
        --user \
        --requirement=/tmp/requirements-dev.txt && \
    rm --force /tmp/requirements*.txt
