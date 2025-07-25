FROM alpine:latest AS builder

WORKDIR /build

RUN apk update && \
    apk add git ninja cmake make patch linux-headers autoconf automake pkgconfig libtool \
    clang llvm lld libc-dev libc++-dev \
    llvm-libunwind llvm-libunwind-static xz-libs xz-dev xz-static \
    ca-certificates curl bash \
    python3 python3-dev \
    zlib-dev zstd-dev  \
    go bzip2 xz unzip

# Clone & build curl-impersonate
RUN git clone --depth 1 --branch v1.1.2 https://github.com/lexiforest/curl-impersonate.git .

ENV CC=clang CXX=clang++

# dynamic build
RUN mkdir /build/install && \
    ./configure --prefix=/build/install \
        --with-zlib --with-zstd \
        --with-ca-path=/etc/ssl/certs \
        --with-ca-bundle=/etc/ssl/certs/ca-certificates.crt && \
    make build && \
    make checkbuild && \
    make install

# static build
RUN ./configure --prefix=/build/install \
        --enable-static \
        --with-zlib --with-zstd \
        --with-ca-path=/etc/ssl/certs \
        --with-ca-bundle=/etc/ssl/certs/ca-certificates.crt && \
    make build && \
    make checkbuild && \
    make install



FROM python:3.13-alpine

ARG RELEASE_VERSION
ENV RELEASE_VERSION=${RELEASE_VERSION}
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_ROOT_USER_ACTION=ignore
ENV PIP_NO_CACHE_DIR=1

RUN apk update && \
    apk add --no-cache ffmpeg su-exec ca-certificates libc++ zstd

COPY . /archivetube

WORKDIR /archivetube

RUN pip install -r requirements.txt
RUN chmod +x init.sh

COPY --from=builder /build/install /usr/local

# Replace /usr/bin/env bash with /usr/bin/env ash
RUN sed -i 's@/usr/bin/env bash@/usr/bin/env ash@' /usr/local/bin/curl_*

EXPOSE 5000

ENTRYPOINT ["./init.sh"]
