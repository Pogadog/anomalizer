"""A Python Pulumi program"""

from pulumi import Output
from pulumi_confluent import KafkaCluster, ConfluentEnvironment, ApiKey
from pulumi_command import local
from pulumi_aws import s3

env = ConfluentEnvironment('test-pulumi-env', name='test-pulumi-env')

cluster = KafkaCluster('test-pulumi-kafka-cluster', name='test-pulumi-kafka-cluster', availability='LOW',
                       service_provider='AWS', region='us-west-2', environment_id=env.id)

api_key = ApiKey('test-pulumi-api-key', environment_id=env.id, cluster_id=cluster.id)

# provision an S3 bucket to allow .csv files to be submitted.
s3_bucket = s3.Bucket('pulumi-upload-bucket')

# use the api-key to launch the anomalizer on localhost talking to the cluster.

def launch_anomalizer(args):
    print('launching anomalizer-service: ' + str(args))
    env = dict(BOOTSTRAP_SERVERS=args[0], SASL_USERNAME=args[1], SASL_PASSWORD=args[2], PROMETHEUS='localhost:9091')
    local.Command('anomalizer-nohup',
                  create='python3 anomalizer-service.py', dir='../python/microservices', environment=env)

Output.all(cluster.bootstrap_servers, api_key.key, api_key.secret).apply(lambda args: launch_anomalizer(args))

