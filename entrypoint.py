#!/usr/bin/env python

# convert start 9:18
# pause 10:13
# start 10:20
# pause 11:02
# start 11:05
# pause 11:12
# start 11:53

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

DEBUG = env.bool("DEBUG",default=False)

if DEBUG:
    LOGLEVEL = logging.DEBUG
else:
    LOGLEVEL = logging.WARNING

logging.basicConfig(stream=sys.stderr, level=LOGLEVEL)
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

print("CW_DIMENSION_NAME ({}) CW_DIMENSION_VALUE ({})".format(CW_DIMENSION_NAME,CW_DIMENSION_VALUE))
# There can be multiple CloudWatch Dimensions, so split into an array
#CW_DIMENSIONS_ARRY= CW_DIMENSIONS.split()
#logger.debug("{} CW_DIMENSIONS is {}".format(datetime.datetime.utcnow(), CW_DIMENSIONS))

# Create Kubernetes scaling url
# KUBE_URL="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_PORT_443_TCP_PORT}/${KUBE_ENDPOINT}"
KUBE_URL="https://{}:{}/{}".format(KUBERNETES_SERVICE_HOST , KUBERNETES_PORT_443_TCP_PORT, KUBE_ENDPOINT)
logger.debug("{} KUBE_URL is {}".format(datetime.datetime.utcnow(), KUBE_URL))

# Set last scaling event time to be far in the past, so initial comparisons work.
# This format works for both busybox and gnu date commands.

# 31536000 seconds is one year.
# KUBE_LAST_SCALING=$(date -u -I'seconds' -d @$(( $(date -u +%s) - 31536000 )))
KUBE_LAST_SCALING=datetime.datetime.utcnow() - datetime.timedelta(days=365) 
logger.debug("{} Last Scalaing is {}".format(datetime.datetime.utcnow(),KUBE_LAST_SCALING))

# printf '%s\n' "$(date -u -I'seconds') Starting autoscaler..."
print('{} Starting autoscaler...'.format( datetime.datetime.utcnow()))

# Exit immediately on signal
# trap 'exit 0' SIGINT SIGTERM EXIT

def handler(signum, frame):
    message='{} Recevied Signal: {}'.format(datetime.datetime.utcnow(),signum)
    logger.error(message)
    raise Exception(message)

signal.signal(signal.SIGINT,  handler)
signal.signal(signal.SIGTERM, handler)
signal.signal(signal.SIGHUP,  handler)
signal.signal(signal.SIGQUIT, handler)

while True:
    logger.debug("{} Tick".format(datetime.datetime.utcnow()))
    time.sleep(CW_POLL_PERIOD)

    try:
        with open('/var/run/secrets/kubernetes.io/serviceaccount/token', 'r') as tokenfile:
            KUBE_TOKEN=tokenfile.read().replace('\n', '')
    except:
        raise Exception('Failed to get TOKEN from /var/run/secrets/kubernetes.io/serviceaccount/token, ServiceAcccount set?')

    http = urllib3.PoolManager(
      ca_certs='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
    )
    r = http.request (
      'GET',
      KUBE_URL,
      headers={
        'Authorization' : 'Bearer {}'.format(KUBE_TOKEN),
        'Accept' : 'application/json'
      }
    )
    data=json.loads(r.data)
    KUBE_CURRENT_REPLICAS = data['spec']['replicas']
    logger.debug('{} Current replicas is {}'.format(datetime.datetime.utcnow(),data['spec']['replicas']))

    boto3.set_stream_logger(name='boto3', level=0, format_string=None)
    cloudwatch=boto3.client('cloudwatch')

    starttime    = pytz.utc.localize(datetime.datetime.utcnow()) - datetime.timedelta(seconds=CW_PERIOD)
    starttimestr = starttime.strftime('%Y-%m-%dT%H:%M:%S%Z')

    endtime      = pytz.utc.localize(datetime.datetime.utcnow())
    endtimestr   = endtime.strftime('%Y-%m-%dT%H:%M:%S%Z')

    logger.debug('{} STARTTIME {}'.format(datetime.datetime.utcnow(),starttimestr))
    logger.debug('{} ENDTIME {}'.format(datetime.datetime.utcnow(),endtimestr))

    logger.debug('get_metric_statistics( Namespace={} , CW_DIMENSION_NAME={} CW_DIMENSION_VALUE={} MetricName={} StartTime={} EndTime={} Period={} Statistics={})'.format(CW_NAMESPACE, CW_DIMENSION_NAME, CW_DIMENSION_VALUE, CW_METRIC_NAME, starttimestr, endtimestr, CW_PERIOD, CW_STATISTICS))

    response = cloudwatch.get_metric_statistics(
        Namespace=CW_NAMESPACE,
        Dimensions=[
            {
            'Name': 'LoadBalancerName',
            'Value': 'af09614cf860a11e8a624020685d3d74'
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
        print "AWS CloudWatch Metric returned no datapoints. If metric exists and container has aws auth, then period may be set too low. Namespace: {} MetricName: {} Dimensions: {} Statistics: {} Period: {} Output: {}".format( CW_NAMESPACE, CW_METRIC_NAME, CW_DIMENSIONS_ARRAY, CW_STATISTICS, CW_PERIOD, response['Datapoints'])
    except IndexError:
        pp.pprint(response)

    else:
        CW_VALUE = response['Datapoints'][0][CW_STATISTICS]
    logger.debug('{} CW_VALUE {}'.format(datetime.datetime.utcnow(),CW_VALUE))
    print('{} CW_VALUE {}'.format(datetime.datetime.utcnow(),CW_VALUE))

    if CW_VALUE <= CW_SCALE_DOWN_VALUE:
        print("CW_VALUE({}) <= CW_SCALE_DOWN_VALUE({})".format(CW_VALUE,CW_SCALE_DOWN_VALUE))
        print("maybe Scale down, replica count?")
        if KUBE_CURRENT_REPLICAS > KUBE_MIN_REPLICAS:
            print("KUBE_CURRENT_REPLICAS ({}) > KUBE_MIN_REPLICAS ({}), cool down passed?".format(KUBE_CURRENT_REPLICAS, KUBE_MIN_REPLICAS))
            print("KUBE_LAST_SCALING ({})".format(KUBE_LAST_SCALING))
            if KUBE_LAST_SCALING < datetime.datetime.utcnow() - datetime.timedelta(seconds=KUBE_SCALE_DOWN_COOLDOWN):
                print("passed scale down cooldown ({})".format(KUBE_SCALE_DOWN_COOLDOWN))
                print("scale down!")
                NEW_REPLICAS=KUBE_CURRENT_REPLICAS - KUBE_SCALE_DOWN_COUNT
                print("Scaling down from {} to {}".format(KUBE_CURRENT_REPLICAS,NEW_REPLICAS))
                PAYLOAD="[{{\"op\":\"replace\",\"path\":\"/spec/replicas\",\"value\":{}}}]".format(NEW_REPLICAS)
                print("PAYLOAD: {}".format(PAYLOAD))
                print("http.request ( 'PATCH', {}, headers={{ 'Authorization' : 'Bearer {}', 'Accept' : 'application/json' }}, body={})".format(KUBE_URL,KUBE_TOKEN,PAYLOAD))

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
                print("type r:{}".format(type(r)))
                print("data r:{}".format(r.data))
                # pp.pprint(r.text)

                #if r.code == 200:
                #    print("Success")
                #else:
                #    print("uh oh: reponse({})".format(r.code))
                data=json.loads(r.data)
                pp.pprint(data)
                datetime.datetime.utcnow()
            else:
                print("did not pass scale down cooldown ({}): no scale".format(KUBE_SCALE_DOWN_COOLDOWN))
        else:
            print("KUBE_CURRENT_REPLICAS ({}) !> KUBE_MIN_REPLICAS({}): no scale".format(KUBE_CURRENT_REPLICAS,KUBE_MIN_REPLICAS))
    elif CW_SCALE_UP_VALUE > CW_VALUE > CW_SCALE_DOWN_VALUE:
        print("Do nothing CW_SCALE_UP_VALUE({}) > CW_VALUE({}) > CW_SCALE_DOWN_VALUE({})".format(CW_SCALE_UP_VALUE,CW_VALUE,CW_SCALE_DOWN_VALUE))

    elif CW_VALUE >= CW_SCALE_UP_VALUE:
        print("CW_VALUE({}) >= CW_SCALE_UP_VALUE({})".format(CW_VALUE,CW_SCALE_UP_VALUE))
        print("maybe Scale up, replica count?")
        if KUBE_CURRENT_REPLICAS < KUBE_MAX_REPLICAS:
            print("KUBE_CURRENT_REPLICAS ({}) < KUBE_MAX_REPLICAS({}), cool down passed?".format( KUBE_CURRENT_REPLICAS, KUBE_MAX_REPLICAS))
            print("KUBE_LAST_SCALING ({})".format(KUBE_LAST_SCALING))
            if KUBE_LAST_SCALING < datetime.datetime.utcnow() - datetime.timedelta(seconds=KUBE_SCALE_UP_COOLDOWN):
                print("passwed scale up cooldown ({})".format(KUBE_SCALE_UP_COOLDOWN))
                print("scale up!")
                NEW_REPLICAS=KUBE_CURRENT_REPLICAS + KUBE_SCALE_UP_COUNT
                print("Scaling up from {} to {}".format(KUBE_CURRENT_REPLICAS,NEW_REPLICAS))
                PAYLOAD="[{{\"op\":\"replace\",\"path\":\"/spec/replicas\",\"value\":{}}}]".format(NEW_REPLICAS)
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
		print("type r:{}".format(type(r)))
                print("data r:{}".format(r.data))
                KUBE_LAST_SCALING=datetime.datetime.utcnow()
            else:
                print("waiting on scala up cooldown")
    else:
        print("Bad values: CW_SCALE_UP_VALUE({}) CW_VALUE({}) CW_SCALE_DOWN_VALUE({})".format(CW_SCALE_UP_VALUE,CW_VALUE,CW_SCALE_DOWN_VALUE))


#
#    # If the metric value is <= the scale-down value, and current replica count is > min replicas, and the last time we scaled up or down was at least the cooldown period ago
#    if [[ "${CW_VALUE}" -le "${CW_SCALE_DOWN_VALUE}"  &&  "${KUBE_CURRENT_REPLICAS}" -gt "${KUBE_MIN_REPLICAS}"  &&  "${KUBE_LAST_SCALING}" < $(date -u -I'seconds' -d @$(( $(date -u +%s) - ${KUBE_SCALE_DOWN_COOLDOWN} ))) ]]; then
#        NEW_REPLICAS=$(( ${KUBE_CURRENT_REPLICAS} - ${KUBE_SCALE_DOWN_COUNT} ))
#        NEW_REPLICAS=$(( ${NEW_REPLICAS} > ${KUBE_MIN_REPLICAS} ? ${NEW_REPLICAS} : ${KUBE_MIN_REPLICAS} ))
#        printf '%s\n' "$(date -u -I'seconds') Scaling down from ${KUBE_CURRENT_REPLICAS} to ${NEW_REPLICAS}"
#        PAYLOAD='[{"op":"replace","path":"/spec/replicas","value":'"${NEW_REPLICAS}"'}]'
#        SCALE_OUTPUT=$(curl -sS --cacert "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt" -H "Authorization: Bearer ${KUBE_TOKEN}" -X PATCH -H 'Content-Type: application/json-patch+json' --data "${PAYLOAD}" "${KUBE_URL}")
#        if [[ "${?}" -ne 0 ]]; then
#            printf '%s\n' "$(date -u -I'seconds') Exiting: Unable to patch kubernetes deployment. Payload:${PAYLOAD} OUTPUT:${SCALE_OUTPUT}"
#            exit 1 # Kube will restart this pod
#        fi
#        # Confirm response says correct number of replicas, instead of an error message
#        SCALE_REPLICAS=$(printf '%s' "${SCALE_OUTPUT}" | jq '.spec.replicas')
#        if [[ "${SCALE_REPLICAS}" -ne "${NEW_REPLICAS}" ]]; then
#            printf '%s\n' "$(date -u -I'seconds') Exiting: Unable to patch kubernetes deployment. Payload:${PAYLOAD} OUTPUT:${SCALE_OUTPUT}"
#            exit 1 # Kube will restart this pod
#        fi
#        KUBE_LAST_SCALING=$(date -u -I'seconds')
#    fi
#
#    # If the metric value is >= the scale-up value, and current replica count is < max replicas, and the last time we scaled up or down was at least the cooldown period ago
#    if [[ "${CW_VALUE}" -ge "${CW_SCALE_UP_VALUE}"  &&  "${KUBE_CURRENT_REPLICAS}" -lt "${KUBE_MAX_REPLICAS}"  &&  "${KUBE_LAST_SCALING}" < $(date -u -I'seconds' -d @$(( $(date -u +%s) - ${KUBE_SCALE_UP_COOLDOWN} ))) ]]; then
#        NEW_REPLICAS=$(( ${KUBE_CURRENT_REPLICAS} + ${KUBE_SCALE_UP_COUNT} ))
#        NEW_REPLICAS=$(( ${NEW_REPLICAS} < ${KUBE_MAX_REPLICAS} ? ${NEW_REPLICAS} : ${KUBE_MAX_REPLICAS} ))
#        printf '%s\n' "$(date -u -I'seconds') Scaling up from ${KUBE_CURRENT_REPLICAS} to ${NEW_REPLICAS}"
#        PAYLOAD='[{"op":"replace","path":"/spec/replicas","value":'"${NEW_REPLICAS}"'}]'
#        SCALE_OUTPUT=$(curl -sS --cacert "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt" -H "Authorization: Bearer ${KUBE_TOKEN}" -X PATCH -H 'Content-Type: application/json-patch+json' --data "${PAYLOAD}" "${KUBE_URL}")
#        if [[ "${?}" -ne 0 ]]; then
#            printf '%s\n' "$(date -u -I'seconds') Exiting: Unable to patch kubernetes deployment. Payload:${PAYLOAD} OUTPUT:${SCALE_OUTPUT}"
#            exit 1 # Kube will restart this pod
#        fi
#        # Confirm response says correct number of replicas, instead of an error message
#        SCALE_REPLICAS=$(printf '%s' "${SCALE_OUTPUT}" | jq '.spec.replicas')
#        if [[ "${SCALE_REPLICAS}" -ne "${NEW_REPLICAS}" ]]; then
#            printf '%s\n' "$(date -u -I'seconds') Exiting: Unable to patch kubernetes deployment. Payload:${PAYLOAD} OUTPUT:${SCALE_OUTPUT}"
#            exit 1 # Kube will restart this pod
#        fi
#        KUBE_LAST_SCALING=$(date -u -I'seconds')
#    fi
#
#done

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
