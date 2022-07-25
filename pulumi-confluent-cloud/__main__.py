"""A Python Pulumi program"""

from pulumi import Output, ResourceOptions
from pulumi_confluent import KafkaCluster, ConfluentEnvironment, ApiKey
from pulumi_command import local
from pulumi_aws import s3
import confluent_cli as cli

env = ConfluentEnvironment('test-pulumi-env', name='test-pulumi-env')

cluster = KafkaCluster('test-pulumi-kafka-cluster', name='test-pulumi-kafka-cluster', availability='LOW',
                       service_provider='AWS', region='us-west-2', environment_id=env.id)

api_key = ApiKey('test-pulumi-api-key', environment_id=env.id, cluster_id=cluster.id)

# provision an S3 bucket to allow .csv files to be submitted.
#s3_bucket = s3.Bucket('pulumi-upload-bucket')

# use the api-key to launch the anomalizer on localhost talking to the cluster.

def launch_anomalizer(args):
    print('launching anomalizer-service: ' + str(args))
    env = dict(BOOTSTRAP_SERVERS=args[0], SASL_USERNAME=args[1], SASL_PASSWORD=args[2], PROMETHEUS='localhost:9091')
    local.Command('anomalizer-nohup',
                  create='python3 anomalizer-service.py', dir='../python/microservices', environment=env)

#Output.all(cluster.bootstrap_servers, api_key.key, api_key.secret).apply(lambda args: launch_anomalizer(args))

# bring up a cluster
cli.ksqldb('anomalizer-ksqldb-1', env, api_key, cluster)

from pulumi.dynamic import ResourceProvider, CreateResult
from ksql import KSQLAPI

class KSQLProvider(ResourceProvider):
    def create(self, inputs):
        client = KSQLAPI(endpoint, api_key=api_key, secret=api_secret)
        return client.ksql(cmd)
        return CreateResult(id_="foo", outs={})

#cmd = "CREATE STREAM log_stream (name STRING, levelame STRING) WITH (kafka_topic='loki-anomalizer', partitions=1, value_format='JSON');"
#cli.ksqldb_command('stream_users_1', cmd)

cli.ksqldb_command('show_streams', 'show streams', api_key)
