FROM alpine:3.8
LABEL maintainer="Chris Duncan <github.com/veqryn>"

RUN set -eux; \
  apk update; \
  apk upgrade; \
  apk add --update --no-cache tzdata ca-certificates curl jq bash less python python-dev py-pip vim build-base libffi libffi-dev openssl-dev; \
  rm -rf /var/cache/apk/* /tmp/* /var/tmp/*; 

RUN pip install --upgrade pip; \
    pip install awscli --upgrade --user;

RUN  mkdir -p /myapp
ADD entrypoint.py /myapp
ADD showenv.sh /myapp
ADD requirements.txt /myapp
WORKDIR /myapp
RUN pip install -r requirements.txt; \
    chmod 755 entrypoint.py; \
    # /usr/sbin/addgroup -g 999 appuser && \  # in alpine 999 is the "ping" group, but no files have this group, odd
    /usr/sbin/adduser -h /myapp -s i/bin/fasle -g ping -D appuser ; \
    chown appuser:appuser entrypoint.py showenv.sh requirements.txt
USER appuser

CMD ["/myapp/entrypoint.py"]
