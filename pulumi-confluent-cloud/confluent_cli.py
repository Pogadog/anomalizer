# implement useful confluent-cli commands as local.Command() wrapped objects.

import subprocess, os
import sys, json

from pulumi import ResourceOptions, Output
from pulumi_command import local
import time

def wait_for_state(cluster_name, env_id, state):
    done = False
    while not done:
        list = subprocess.run(f'confluent ksql cluster list -o json --environment {env_id}', shell=True, capture_output=True).stdout
        if not list:
            print('unable to load cluster list')
            continue
        clusters = json.loads(list.decode())
        found = False
        for cluster in clusters:
            if cluster['name']==cluster_name:
                found = True
                print(cluster)
                if cluster['status'] == state:
                    done = True
                    break
        if state=='ABSENT' and not found:
            break
        print('waiting for 1 seccond to be: ' + state)
        time.sleep(1)


def create(cluster_name, env_id, api_key, api_secret, cluster_id):
    subprocess.run(f'confluent ksql cluster create {cluster_name} --environment {env_id} --api-key {api_key} --api-secret {api_secret} --cluster {cluster_id}', shell=True)
    wait_for_state(cluster_name, env_id, 'UP')

def delete(cluster_name, env_id):
    stdout = subprocess.run(f'confluent ksql cluster list -o json --environment {env_id}', shell=True, capture_output=True).stdout
    if stdout:
        clusters = json.loads(stdout.decode())
        # find the cluster id by name
        cluster_id = ''
        for cluster in clusters:
            if cluster['name']==cluster_name:
                cluster_id = cluster['id']
                break
        if cluster_id:
            subprocess.run(f'confluent ksql cluster delete {cluster_id} --environment {env_id}', shell=True, capture_output=True)
            print(f'delete! cluster_name={cluster_name}, cluster_id={cluster_id}, env_id={env_id}')
            wait_for_state(cluster_name, env_id, 'ABSENT')

def ksqldb(cluster_name, env, api, cluster):
    Output.all(env.id, api.key, api.secret, cluster.id).apply(lambda args:
        local.Command(
            resource_name=cluster_name,
            opts=ResourceOptions(depends_on=[env, api, cluster]),
            create=f'python -c "import confluent_cli as cli; cli.create(\'{cluster_name}\', \'{args[0]}\', \'{args[1]}\', \'{args[2]}\', \'{args[3]}\')"',
            delete=f'python -c "import confluent_cli as cli; cli.delete(\'{cluster_name}\', \'{args[0]}\')"',
        )
    )

from ksql import KSQLAPI

def ksqldb_invoke(cmd, api_key, api_secret):
    # invoke command using rest api on the endpoint created in ksqldb
    endpoint = os.environ.get('CONFLUENT_ENDPOINT')
    #api_key = os.environ.get('CONFLUENT_KEY')
    #api_secret = os.environ.get('CONFLUENT_SECRET')
    client = KSQLAPI(endpoint, api_key=api_key, secret=api_secret)
    client.ksql(cmd)
    #client.ksql('CREATE STREAM users (id INTEGER KEY, gender STRING, name STRING, age INTEGER) WITH (kafka_topic=\'users\', partitions=1, value_format=\'JSON\');')

def ksqldb_command(cmd_name, cmd, api):
      #cmd = cmd.replace("'", "\\'") # escape single quotes.
      Output.all(api.key, api.secret).apply(lambda args:
          local.Command(
              resource_name=cmd_name,
              create=f'python -c "import confluent_cli as cli; cli.ksqldb_invoke(\'{cmd}\', \'{args[0]}\', \'{args[1]}\')"',
          )
      )


'''
def create_cluster(env_id, cluster_name, api_key, api_secret, cluster_id):
    subprocess.run(f'confluent ksql cluster create -o json {cluster_name} --environment {env_id} --api-key {api_key} --api-secret {api_secret} --cluster {cluster_id}', shell=True)
    # TODO: hook stdout and wait for the cluster to transition to a running state.

def delete_cluster(env_id, cluster_id):
    subprocess.run(f'confluent ksql cluster delete --environment {env_id} {cluster_id}', shell=True)
    # TODO: hook stdout and wait for the cluster to transition to a running state.

def ksqldb(env_id, cluster_name, api_key, api_secret, cluster_id):
    print(f'ksqld: env_id={env_id}, cluster_name={cluster_name}, api_key={api_key}, api_secret={api_secret}, cluster_id={cluster_id}')
    command = local.Command(
        resource_name=cluster_name,
        create=f'python confluent_cli.py create_cluster {env_id} {cluster_name} {api_key} {api_secret} {cluster_id}',
        delete=f'python confluent_cli.py delete_cluster {env_id} {cluster_id}',
    )

if __name__=='__main__':
    print(sys.argv)
    if sys.argv[1]=='create_cluster':
        create_cluster(*sys.argv[2:])
    if sys.argv[1]=='delete_cluster':
        delete_cluster(*sys.argv[2:])

'''