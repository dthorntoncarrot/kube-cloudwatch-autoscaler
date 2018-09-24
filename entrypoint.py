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
import time
import logging
from envparse import env
from kubernetes import client, config
import urllib.request
import boto3
import pprint

DEBUG = env.bool("DEBUG",default=False)

if DEBUG:
    LOGLEVEL = logging.DEBUG
else:
    LOGLEVEL = logging.WARNING

logging.basicConfig(stream=sys.stderr, level=LOGLEVEL)
logger = logging.getLogger()

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
CW_STATISTIC              = env( 'CW_STATISTIC',             cast=str,   default="Average")
CW_PERIOD                 = env( 'CW_PERIOD',                cast=int,   default=360)
CW_POLL_PERIOD            = env( 'CW_POLL_PERIOD',           cast=int,   default=30)

# Provided by the KUBE Env ( not required in deployment env config or configmap )
KUBERNETES_SERVICE_HOST      = env('KUBERNETES_SERVICE_HOST',      cast=str, default='kubernetes.default.svc')
KUBERNETES_PORT_443_TCP_PORT = env('KUBERNETES_PORT_443_TCP_PORT', cast=str, default='443')

# There can be multiple CloudWatch Dimensions, so split into an array
CW_DIMENSIONS_ARRY= CW_DIMENSIONS.split()
logger.debug("{} CW_DIMENSIONS is {}".format(datetime.datetime.utcnow(), CW_DIMENSIONS))

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
        KUBE_TOKEN='dummy'
    logger.debug('{} Token {}'.format(datetime.datetime.utcnow(),KUBE_TOKEN))

    config.load_kube_config()
    v1 = client.CoreV1Api()
    


#
#    # Query kubernetes pod/deployment current replica count
#    KUBE_CURRENT_OUTPUT=$(curl -sS --cacert "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt" -H "Authorization: Bearer ${KUBE_TOKEN}" "${KUBE_URL}")
#    if [[ "${?}" -ne 0 ]]; then
#        printf '%s\n' "$(date -u -I'seconds') Exiting: Unable to query kubernetes service account. URL:${KUBE_URL} Output:${KUBE_CURRENT_OUTPUT}"
#        exit 1 # Kube will restart this pod
#    fi
#
#    KUBE_CURRENT_REPLICAS=$(printf '%s' "${KUBE_CURRENT_OUTPUT}" | jq 'first(.spec.replicas, .status.replicas | numbers)')
#    if [[ -z "${KUBE_CURRENT_REPLICAS}" || "${KUBE_CURRENT_REPLICAS}" == "null" ]]; then
#        printf '%s\n' "$(date -u -I'seconds') Exiting: Kubernetes service account not showing .spec.replicas or .status.replicas: ${KUBE_CURRENT_OUTPUT}"
#        exit 1 # Kube will restart this pod
#    fi
#    if [ "${VERBOSE}" = true ]; then
#        printf '%s\n' "$(date -u -I'seconds') Kube Replicas: ${KUBE_CURRENT_REPLICAS}"
#    fi
#
#    # Query aws cloudwatch metric
#    CW_OUTPUT=$(aws cloudwatch get-metric-statistics --namespace "${CW_NAMESPACE}" --metric-name "${CW_METRIC_NAME}" --dimensions "${CW_DIMENSIONS_ARRAY[@]}" --start-time $(date -u -I'seconds' -d @$(( $(date -u +%s) - ${CW_PERIOD} ))) --end-time $(date -u -I'seconds') --statistics "${CW_STATISTICS}" --period "${CW_PERIOD}")
#    if [[ "${?}" -ne 0 ]]; then
#        printf '%s\n' "$(date -u -I'seconds') Exiting: Unable to query AWS CloudWatch Metric: ${CW_OUTPUT}"
#        exit 1 # Kube will restart this pod
#    fi
#
#    CW_VALUE=$(printf '%s' "${CW_OUTPUT}" | jq ".Datapoints[0].${CW_STATISTICS} | numbers")
#    if [[ -z "${CW_VALUE}" || "${CW_VALUE}" == "null" ]]; then
#        printf '%s\n' "$(date -u -I'seconds') AWS CloudWatch Metric returned no datapoints. If metric exists and container has aws auth, then period may be set too low. Namespace:${CW_NAMESPACE} MetricName:${CW_METRIC_NAME} Dimensions:${CW_DIMENSIONS_ARRAY[@]} Statistics:${CW_STATISTICS} Period:${CW_PERIOD} Output:${CW_OUTPUT}"
#        continue
#    fi
#    # CloudWatch metrics can have decimals, but bash doesn't like them, so remove with printf
#    CW_VALUE=$(printf '%.0f' "${CW_VALUE}")
#    if [ "${VERBOSE}" = true ]; then
#        printf '%s\n' "$(date -u -I'seconds') AWS CloudWatch Value: ${CW_VALUE}"
#    fi
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
