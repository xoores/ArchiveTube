FROM lexiforest/curl-impersonate:alpine
#FROM python:3.13-alpine

ARG RELEASE_VERSION
ENV RELEASE_VERSION=${RELEASE_VERSION}
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_ROOT_USER_ACTION=ignore
ENV PIP_NO_CACHE_DIR=1

RUN apk update && apk add --no-cache ffmpeg su-exec

COPY . /archivetube

WORKDIR /archivetube

RUN pip install -r requirements.txt
RUN chmod +x init.sh

EXPOSE 5000

ENTRYPOINT ["./init.sh"]
