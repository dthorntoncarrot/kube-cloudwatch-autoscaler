FROM alpine:3.8
LABEL maintainer="Chris Duncan <github.com/veqryn>"

# Install software requirements
RUN set -eux; \
  apk update; \
  apk upgrade; \
  apk add --update --no-cache tzdata ca-certificates curl jq bash less python python-dev py-pip vim build-base libffi libffi-dev openssl-dev; \
  apk add --update --no-cache --repository https://dl-3.alpinelinux.org/alpine/edge/testing aws-cli; \
  pip install --upgrade pip; \
  rm -rf /var/cache/apk/* /tmp/* /var/tmp/*

ADD . /myapp
WORKDIR /myapp
RUN pip install -r requirements.txt; \
    chmod 755 entrypoint.py
CMD ["/myapp/entrypoint.py"]
