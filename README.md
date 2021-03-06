# kube-cloudwatch-autoscaler
This is a Kubernetes deployment that will manage the autoscaling of one other Kubernetes deployment/replica/pod, periodically scaling the number of replicas based on any AWS CloudWatch metric (ex: SQS Queue Size or Max Age, ELB Response Time, etc).
An example would be using it to increase the number of pods when the age of the oldest message on SQS gets too old, and decrease the number of pods when it stabilizes again.

Docker image can be found on Docker Hub (https://hub.docker.com/r/veqryn/kube-cloudwatch-autoscaler/) and Docker Cloud (https://cloud.docker.com/swarm/veqryn/repository/docker/veqryn/kube-cloudwatch-autoscaler).

## How to use:
1. Ensure this autoscaler will have the necessary AWS permissions to access CloudWatch.
    * You may either use 'AWS EC2 Roles', or create a user with an access token. 
2. Create the below deployment in your Kubernetes cluster, after changing the variables to suite your needs.

### Kubernetes deployment yaml (with default values for optional variables)

```yaml
apiVersion: extensions/v1beta1
kind: Deployment
metadata:
  name: kube-cloudwatch-autoscaler
  labels:
    app: kube-cloudwatch-autoscaler
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kube-cloudwatch-autoscaler
  template:
    metadata:
      labels:
        app: kube-cloudwatch-autoscaler
    spec:
      containers:
        - name: kube-cloudwatch-autoscaler
          image: "veqryn/kube-cloudwatch-autoscaler:1.1"
          env:
            - name: KUBE_ENDPOINT # Required, the app's api endpoint in kube (this example will cause us to scale a deployment named "my-app-name")
              value: "apis/apps/v1beta1/namespaces/default/deployments/my-app-name/scale"
            - name: KUBE_MIN_REPLICAS # Optional
              value: "1"
            - name: KUBE_MAX_REPLICAS # Optional
              value: "50"
            - name: KUBE_SCALE_DOWN_COUNT # Optional, how many replicas to reduce by when scaling down
              value: "1"
            - name: KUBE_SCALE_UP_COUNT # Optional, how many replicas to increase by when scaling up
              value: "1"
            - name: KUBE_SCALE_DOWN_COOLDOWN # Optional, cooldown in seconds after scaling down
              value: "180"
            - name: KUBE_SCALE_UP_COOLDOWN # Optional, cooldown in seconds after scaling up
              value: "300"
            - name: CW_SCALE_DOWN_VALUE # Required, cloudwatch metric value that will trigger scaling down
              value: "300"
            - name: CW_SCALE_UP_VALUE # Required, cloudwatch metric value that will trigger scaling up
              value: "900"
            - name: CW_NAMESPACE # Required (see https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html)
              value: "AWS/SQS"
            - name: CW_METRIC_NAME # Required
              value: "ApproximateAgeOfOldestMessage"
            - name: CW_DIMENSIONS # Required (Separate multiple dimensions with spaces, such as: "Name=TargetGroup,Value=targetgroup/my-tg/abc Name=LoadBalancer,Value=app/my-elb/xyz")
              value: "Name=QueueName,Value=my_sqs_queue_name"
            - name: CW_STATISTICS # Optional, how to aggregate data if there are multiple within a period (Average, Sum, Minimum, Maximum, SampleCount, or pNN.NN)
              value: "Average"
            - name: CW_PERIOD # Optional, the length of time in seconds to search for and aggregate datapoints (should be longer than how often cloudwatch is populated with new datapoints)
              value: "360"
            - name: CW_POLL_PERIOD # Optional, how often to poll cloudwatch for new data, and possibly scale up or down
              value: "30"
            - name: VERBOSE # Optional, will log kube and cloudwatch statistics
              value: "false"
            - name: AWS_DEFAULT_REGION # Optional, Needed only if not using AWS EC2 Roles
              value: "us-east-1"
            - name: AWS_ACCESS_KEY_ID # Optional, Needed only if not using AWS EC2 Roles
              valueFrom:
                secretKeyRef:
                  name: aws-secrets
                  key: aws-access-key-id
            - name: AWS_SECRET_ACCESS_KEY # Optional, Needed only if not using AWS EC2 Roles
              valueFrom:
                secretKeyRef:
                  name: aws-secrets
                  key: aws-secret-access-key
          resources:
            requests:
              memory: 24Mi
              cpu: 10m
            limits:
              memory: 48Mi
              cpu: 50m

---
# Optional, Needed only if not using AWS EC2 Roles
kind: Secret
metadata:
  name: aws-secrets
  labels:
    app: aws-secrets
type: Opaque
data:
  aws-access-key-id: "YXdzLWtleQ=="
  aws-secret-access-key: "YXdzLXNlY3JldA=="
```

if you are not using the aws secrets , and are instead using an IAM instance profile , the nyou must remove the secret lines from the deployment file.

### AWS Permissions

Create the following policy ( call it "kubernetes-cluster-autoscaler " ) and attach to the role or user you used above, or the iam instance profile.
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "autoscaling:DescribeAutoScalingGroups",
                "autoscaling:DescribeAutoScalingInstances",
                "autoscaling:DescribeTags",
                "autoscaling:DescribeLaunchConfigurations",
                "autoscaling:SetDesiredCapacity",
                "autoscaling:TerminateInstanceInAutoScalingGroup",
                "cloudwatch:GetMetricStatistics"
            ],
            "Resource": "*"
        }
    ]
}
```

### The python implemention

When I got my hands on this code I was pretty impressed that it was so tight. Basically one bash scripts and good docs gives you a powerful piece of glue.

But I hit a bug/feature : I wanted to scale on elb latency which is reporting in seconds, orratehr during normal circumstances, fractions of seconds, decimal places of seconds. The bash sciript could not handle that.

I decided that the best way to fix it would be to re-implement the script in python. The hard part has been done by Chris Duncan (veqryn), I could just go line by line and convert to python.

That's what I've done here.

I used python virtenv to get this to work and the make the dependancies clear.

#### Added features

NOOP if set, don't do anything, just talk about it. all replica count altering activity is subverted.

DEBUG if set say more about what you are doing.

### Building the iamge

from the working dir:

 docker build -f python.Dockerfile . -t carrotrewards/kube-cloudwatch-autoscaler:latest

 docker push carrotrewards/kube-cloudwatch-autoscaler:latest

### To do / wishlist

* Put the cooldown checks before the cw metric fetch.
* get th script to figure out the load balancer name from the k tags ( for example "kubernetes.io/service-name" = "servicename"

