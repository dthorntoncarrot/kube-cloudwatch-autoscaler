#!/usr/bin/env python

# This script will periodically scale the number of replicas
# of a given kubernetes deployment up or down,
# determined by the value of an aws cloudwatch metric.

import sys
import os
import signal
import datetime
import pytz
import time
import logging
from envparse import env
from kubernetes import client, config
import urllib3.request
import boto3
import pprint
import gzip
import json
import re

DEBUG = env("DEBUG", cast=str, default="DEBUG")
NOOP = env.bool("NOOP",default=False)

if DEBUG == "DEBUG":
    LOGLEVEL = logging.DEBUG
elif DEBUG == "INFO":
    LOGLEVEL = logging.INFO
else:
    LOGLEVEL = logging.WARNING

logging.basicConfig(stream=sys.stderr, level=LOGLEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

pp = pprint.PrettyPrinter(indent=4)

KUBE_ENDPOINT             = env( 'KUBE_ENDPOINT',            cast=str, default="Required: KUBE_ENDPOINT must be equal to the Kube API scaling endpoint for the deployment, such as: 'apis/apps/v1beta1/namespaces/default/deployments/<MyAppName>/scale")
KUBE_MIN_REPLICAS         = env( 'KUBE_MIN_REPLICAS',        cast=int,   default=1)
KUBE_MAX_REPLICAS         = env( 'KUBE_MAX_REPLICAS',        cast=int,   default=50)
KUBE_SCALE_DOWN_COUNT     = env( 'KUBE_SCALE_DOWN_COUNT',    cast=int,   default=1)
KUBE_SCALE_DOWN_COOLDOWN  = env( 'KUBE_SCALE_DOWN_COOLDOWN', cast=int,   default=180)
KUBE_SCALE_UP_COUNT       = env( 'KUBE_SCALE_UP_COUNT',      cast=int,   default=1)
KUBE_SCALE_UP_COOLDOWN    = env( 'KUBE_SCALE_UP_COOLDOWN',   cast=int,   default=300)

CW_SCALE_DOWN_VALUE       = env( 'CW_SCALE_DOWN_VALUE',      cast=float, default="Required: CW_SCALE_DOWN_VALUE must be set to the AWS CloudWatch metric value that will trigger scaling down the replicas, such as '300'" )
CW_SCALE_UP_VALUE         = env( 'CW_SCALE_UP_VALUE',        cast=float, default="Required: CW_SCALE_UP_VALUE must be set to the AWS CloudWatch metric value that will trigger scaling up the replicas, such as '900'")
CW_NAMESPACE              = env( 'CW_NAMESPACE' ,            cast=str,   default="Required: CW_NAMESPACE must be set to the AWS CloudWatch Namespace, such as: 'AWS/SQS'")
CW_METRIC_NAME            = env( 'CW_METRIC_NAME',           cast=str,   default="Required: CW_METRIC_NAME must be set to the AWS CloudWatch MetricName, such as: 'ApproximateAgeOfOldestMessage'" )
CW_DIMENSIONS             = env( 'CW_DIMENSIONS',            cast=str,   default="Required: CW_DIMENSIONS must be set to the AWS CloudWatch Dimensions, such as: 'Name=QueueName,Value=my_sqs_queue_name'")
CW_STATISTICS             = env( 'CW_STATISTICS',            cast=str,   default="Average")
CW_PERIOD                 = env( 'CW_PERIOD',                cast=int,   default=360)
CW_POLL_PERIOD            = env( 'CW_POLL_PERIOD',           cast=int,   default=30)

# Provided by the KUBE Env ( not required in deployment env config or configmap )
KUBERNETES_SERVICE_HOST      = env('KUBERNETES_SERVICE_HOST',      cast=str, default='kubernetes.default.svc')
KUBERNETES_PORT_443_TCP_PORT = env('KUBERNETES_PORT_443_TCP_PORT', cast=str, default='443')

# for now only do one dimention with Name / Value
#pattern = re.compile('Name=(?P<name>.*?),Value=(?P<value>.*?)',re.VERBOSE)
#match = pattern.match(CW_DIMENSIONS)
#CW_DIMENSION_NAME = match.group("name")
#CW_DIMENSION_VALUE = match.group("value")

pattern = re.compile('Name=(.*),Value=(.*)',re.VERBOSE)
match = pattern.match(CW_DIMENSIONS)
CW_DIMENSION_NAME  = match.group(1)
CW_DIMENSION_VALUE = match.group(2)

logger.info("CW_DIMENSION_NAME ({}) CW_DIMENSION_VALUE ({})".format(CW_DIMENSION_NAME,CW_DIMENSION_VALUE))
# There can be multiple CloudWatch Dimensions, so split into an array
#CW_DIMENSIONS_ARRY= CW_DIMENSIONS.split()
#logger.debug("{} CW_DIMENSIONS is {}".format(datetime.datetime.utcnow(), CW_DIMENSIONS))

# Create Kubernetes scaling url
# KUBE_URL="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_PORT_443_TCP_PORT}/${KUBE_ENDPOINT}"
KUBE_URL="https://{}:{}/{}".format(KUBERNETES_SERVICE_HOST , KUBERNETES_PORT_443_TCP_PORT, KUBE_ENDPOINT)
logger.debug("KUBE_URL is {}".format(KUBE_URL))

# Set last scaling event time to be far in the past, so initial comparisons work.
# This format works for both busybox and gnu date commands.

# 31536000 seconds is one year.
# KUBE_LAST_SCALING=$(date -u -I'seconds' -d @$(( $(date -u +%s) - 31536000 )))
KUBE_LAST_SCALING=datetime.datetime.utcnow() - datetime.timedelta(days=365) 
logger.debug("Last Scalaing is {}".format(KUBE_LAST_SCALING))

logger.info('Starting autoscaler...')

if CW_SCALE_DOWN_VALUE >= CW_SCALE_UP_VALUE:
    logger.critical("CW_SCALE_DOWN_VALUE ({}) is greater than CW_SCALE_UP_VALUE ({}). This is invalid, exiting".format(CW_SCALE_DOWN_VALUE,CW_SCALE_UP_VALUE))

# Exit immediately on signal
# trap 'exit 0' SIGINT SIGTERM EXIT

def handler(signum, frame):
    logger.error('Recevied Signal: {}'.format(datetime.datetime.utcnow(),signum))
    raise Exception(message)

signal.signal(signal.SIGINT,  handler)
signal.signal(signal.SIGTERM, handler)
signal.signal(signal.SIGHUP,  handler)
signal.signal(signal.SIGQUIT, handler)

while True:
    time.sleep(CW_POLL_PERIOD)
    logger.debug("Tick")

    try:
        with open('/var/run/secrets/kubernetes.io/serviceaccount/token', 'r') as tokenfile:
            KUBE_TOKEN=tokenfile.read().replace('\n', '')
    except:
        raise Exception('Failed to get TOKEN from /var/run/secrets/kubernetes.io/serviceaccount/token, ServiceAcccount set?')

    try:
        http = urllib3.PoolManager(
            ca_certs='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
        )
    except BaseException as e:
        logger.info('Failed to create urllib3.PoolManager: msg: {} arg: {}'.format(e.message,e.args))
        exit

    r = http.request (
      'GET',
      KUBE_URL,
      headers={
        'Authorization' : 'Bearer {}'.format(KUBE_TOKEN),
        'Accept' : 'application/json'
      }
    )

    # urllib3.response desn't have a code or status_code - FIXME
    #if r.status_code != 200:
    #    logger.critical("uh oh: Request for auth token failed: reponse({})".format(r.status_code))
    #    continue

    data=json.loads(r.data)
    KUBE_CURRENT_REPLICAS = data['spec']['replicas']
    logger.debug('Current replicas is {}'.format(data['spec']['replicas']))

    boto3.set_stream_logger(name='boto3', level=0, format_string=None)
    cloudwatch=boto3.client('cloudwatch')

    starttime    = pytz.utc.localize(datetime.datetime.utcnow()) - datetime.timedelta(seconds=CW_PERIOD)
    starttimestr = starttime.strftime('%Y-%m-%dT%H:%M:%S%Z')

    endtime      = pytz.utc.localize(datetime.datetime.utcnow())
    endtimestr   = endtime.strftime('%Y-%m-%dT%H:%M:%S%Z')

    logger.debug('STARTTIME {}'.format(starttimestr))
    logger.debug('ENDTIME {}'.format(endtimestr))

    logger.debug('get_metric_statistics( Namespace={} , CW_DIMENSION_NAME={} CW_DIMENSION_VALUE={} MetricName={} StartTime={} EndTime={} Period={} Statistics={})'.format(CW_NAMESPACE, CW_DIMENSION_NAME, CW_DIMENSION_VALUE, CW_METRIC_NAME, starttimestr, endtimestr, CW_PERIOD, CW_STATISTICS))

    response = cloudwatch.get_metric_statistics(
        Namespace=CW_NAMESPACE,
        Dimensions=[
            {
            'Name': CW_DIMENSION_NAME,
            'Value': CW_DIMENSION_VALUE
            }
        ],
        MetricName=CW_METRIC_NAME,
        StartTime=starttime.strftime('%Y-%m-%dT%H:%M:%S%Z'),                         
        EndTime=endtime.strftime('%Y-%m-%dT%H:%M:%S%Z'), 
        Period=CW_PERIOD,
        Statistics=[CW_STATISTICS]
        )

    try:
        response['Datapoints'][0][CW_STATISTICS]
    except NameError:
        logger.error("AWS CloudWatch Metric returned no datapoints. If metric exists and container has aws auth, then period may be set too low. Namespace: {} MetricName: {} Dimensions: {} Statistics: {} Period: {} Output: {}".format(CW_NAMESPACE, CW_METRIC_NAME, CW_DIMENSIONS_ARRAY, CW_STATISTICS, CW_PERIOD, response['Datapoints']))
        continue
    except IndexError:
        logger.debug(pp.pprint(response))
        continue

    else:
        CW_VALUE = response['Datapoints'][0][CW_STATISTICS]
    logger.info('CW_VALUE {}'.format(CW_VALUE))

    if CW_VALUE <= CW_SCALE_DOWN_VALUE:
        logger.info("{} CW_VALUE({}) <= CW_SCALE_DOWN_VALUE({})".format(datetime.datetime.utcnow(),CW_VALUE,CW_SCALE_DOWN_VALUE))
        logger.info("{} Maybe Scale down replica count?".format(datetime.datetime.utcnow()))

        if KUBE_CURRENT_REPLICAS > KUBE_MIN_REPLICAS:
            logger.info("KUBE_CURRENT_REPLICAS ({}) > KUBE_MIN_REPLICAS ({}), cool down passed?".format(KUBE_CURRENT_REPLICAS, KUBE_MIN_REPLICAS))
            logger.info("KUBE_LAST_SCALING ({})".format(KUBE_LAST_SCALING))
            if ( datetime.datetime.utcnow() - KUBE_LAST_SCALING ) > datetime.timedelta(seconds=KUBE_SCALE_DOWN_COOLDOWN):
                logger.info("Passed scale down cooldown ({} seconds)".format(KUBE_SCALE_DOWN_COOLDOWN))
                logger.warn("Scale down!")
                NEW_REPLICAS=KUBE_CURRENT_REPLICAS - KUBE_SCALE_DOWN_COUNT
                logger.info("{} Scaling down from {} to {}".format(datetime.datetime.utcnow(),KUBE_CURRENT_REPLICAS,NEW_REPLICAS))
                PAYLOAD="[{{\"op\":\"replace\",\"path\":\"/spec/replicas\",\"value\":{}}}]".format(NEW_REPLICAS)
                logger.debug("{} PAYLOAD: {}".format(datetime.datetime.utcnow(),PAYLOAD))
                logger.debug("{} http.request ( 'PATCH', {}, headers={{ 'Authorization' : 'Bearer {}', 'Accept' : 'application/json' }}, body={})".format(datetime.datetime.utcnow(),KUBE_URL,KUBE_TOKEN,PAYLOAD))

                if NOOP:
                    logger.info("{} NOOP set, skipping scale down".format(datetime.datetime.utcnow()))
                    continue

		r = http.request (
                    'PATCH',
                    KUBE_URL,
                    headers={
                        'Authorization' : 'Bearer {}'.format(KUBE_TOKEN),
                        'Accept' : 'application/json',
                        'Content-Type' : 'application/json-patch+json'
                    },
                    body=PAYLOAD
                )

                # urlib3.response doesn't have a code or status_code - FIXME
                # if r.status_code != 200:
                #    logger.critical("uh oh: http request for scale down failed: reponse({})".format(r.status_code))
                #    continue

                logger.debug("{} type r:{}".format(datetime.datetime.utcnow(),type(r)))
                logger.debug("{} data r:{}".format(datetime.datetime.utcnow(),r.data))

                data=json.loads(r.data)
                pp.pprint(data)
                KUBE_LAST_SCALING=datetime.datetime.utcnow()
            else:
                logger.info("waiting on scale down cooldown ({})".format(KUBE_SCALE_DOWN_COOLDOWN))

        elif KUBE_CURRENT_REPLICAS < KUBE_MIN_REPLICAS:
            logger.info("{} KUBE_CURRENT_REPLICAS ({}) < KUBE_MIN_REPLICAS ({}), scale up to min at least".format(datetime.datetime.utcnow(),KUBE_CURRENT_REPLICAS, KUBE_MIN_REPLICAS))
            NEW_REPLICAS=KUBE_MIN_REPLICAS
            PAYLOAD="[{{\"op\":\"replace\",\"path\":\"/spec/replicas\",\"value\":{}}}]".format(NEW_REPLICAS)

            if NOOP:
                logger.info("NOOP set, skipping scale up")
                continue 

            r = http.request (
                'PATCH',
                KUBE_URL,
                headers={
                    'Authorization' : 'Bearer {}'.format(KUBE_TOKEN),
                    'Accept' : 'application/json',
                    'Content-Type' : 'application/json-patch+json'
                },
                body=PAYLOAD
            )

            # urllib3.response doens't havea code or status_code - FIXME
            #if r.status_code != 200:
            #    logger.critical("uh oh: http request for scale up failed: reponse({})".format(r.status_code))
            #    continue

            logger.debug("{} type r:{}".format(datetime.datetime.utcnow(),type(r)))
            logger.debug("{} data r:{}".format(datetime.datetime.utcnow(),r.data))
            data=json.loads(r.data)
            if DEBUG:
               pp.pprint(data)
        else:
            logger.info("{} KUBE_CURRENT_REPLICAS ({}) !> KUBE_MIN_REPLICAS({}): no scale".format(datetime.datetime.utcnow(),KUBE_CURRENT_REPLICAS,KUBE_MIN_REPLICAS))

    elif CW_SCALE_UP_VALUE > CW_VALUE > CW_SCALE_DOWN_VALUE:
        logger.info("{} Do nothing CW_SCALE_UP_VALUE({}) > CW_VALUE({}) > CW_SCALE_DOWN_VALUE({})".format(datetime.datetime.utcnow(),CW_SCALE_UP_VALUE,CW_VALUE,CW_SCALE_DOWN_VALUE))

    elif CW_VALUE >= CW_SCALE_UP_VALUE:
        logger.info("CW_VALUE({}) >= CW_SCALE_UP_VALUE({})".format(CW_VALUE,CW_SCALE_UP_VALUE))
        logger.info("maybe Scale up, replica count?")
        if KUBE_CURRENT_REPLICAS < KUBE_MAX_REPLICAS:
            print("KUBE_CURRENT_REPLICAS ({}) < KUBE_MAX_REPLICAS({}), cool down passed?".format( KUBE_CURRENT_REPLICAS, KUBE_MAX_REPLICAS))
            print("KUBE_LAST_SCALING ({})".format(KUBE_LAST_SCALING))
            if ( datetime.datetime.utcnow() - KUBE_LAST_SCALING ) > datetime.timedelta(seconds=KUBE_SCALE_UP_COOLDOWN):
                logger.info("Passed scale up cooldown ({} seconds)".format(KUBE_SCALE_UP_COOLDOWN))
                logger.info("Scale up!")
                NEW_REPLICAS=KUBE_CURRENT_REPLICAS + KUBE_SCALE_UP_COUNT
                print("Scaling up from {} to {}".format(KUBE_CURRENT_REPLICAS,NEW_REPLICAS))
                PAYLOAD="[{{\"op\":\"replace\",\"path\":\"/spec/replicas\",\"value\":{}}}]".format(NEW_REPLICAS)

                if NOOP:
                    logger.info("NOOP set, skipping scale up")
                    continue

                r = http.request (
                    'PATCH',
                    KUBE_URL,
                    headers={
                        'Authorization' : 'Bearer {}'.format(KUBE_TOKEN),
                        'Accept' : 'application/json',
                        'Content-Type' : 'application/json-patch+json'
                    },
                    body=PAYLOAD
                )

                #if r.status_code != 200:
                #    logger.critical("uh oh: http request for scale up failed: reponse({})".format(r.status_code))
                #    continue

                logger.info("Scale up request response: OK{} REASON:{} CODE:{}".format(r.ok,r.reason,r.status_code))
		logger.debug("type r:{}".format(type(r)))
                logger.debug("data r:{}".format(r.data))
                KUBE_LAST_SCALING=datetime.datetime.utcnow()
            else:
                logger.info("waiting on scale up cooldown ({})".format(KUBE_SCALE_UP_COOLDOWN))
    else:
        print("Bad values: CW_SCALE_UP_VALUE({}) CW_VALUE({}) CW_SCALE_DOWN_VALUE({})".format(CW_SCALE_UP_VALUE,CW_VALUE,CW_SCALE_DOWN_VALUE))

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
