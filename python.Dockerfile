FROM alpine:3.8
LABEL maintainer="Chris Duncan <github.com/veqryn>"

RUN set -eux; \
  apk update; \
  apk upgrade; \
  apk add --update --no-cache tzdata ca-certificates curl jq bash less python python-dev py-pip vim build-base libffi libffi-dev openssl-dev; \
  rm -rf /var/cache/apk/* /tmp/* /var/tmp/*; 

RUN pip install --upgrade pip; \
    pip install awscli --upgrade --user;

# root stuff
RUN mkdir -p /myapp;
RUN /usr/sbin/adduser -h /myapp -s i/bin/false -D appuser; \
    chown appuser:appuser /myapp

# app user
WORKDIR /myapp
USER appuser
ENV PATH="/myapp/.local/bin:${PATH}"
ADD entrypoint.py /myapp
ADD showenv.sh /myapp
ADD requirements.txt /myapp
RUN pip install -r requirements.txt --user;

#    /bin/chmod 755 /myapp/entrypoint.py;

CMD ["/myapp/entrypoint.py"]
