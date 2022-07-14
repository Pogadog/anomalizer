"""A Python Pulumi program"""
# deploys the anomalizer to a local docker by deploying an image with different entry points to stand up
# an overall service system.

import pulumi
import pulumi_docker as docker
import os, shutil

# prepare the docker build playing field, since docker won't work with symlinks.
shutil.rmtree('python/', ignore_errors=True)
shutil.rmtree('docker/', ignore_errors=True)
shutil.copytree('../python', 'python/', symlinks=True, dirs_exist_ok=True)
shutil.copytree('../docker', 'docker/', symlinks=True, dirs_exist_ok=True)

app_image = docker.Image("anomalizer_image",
                         build=docker.DockerBuild(context=".", dockerfile="docker/Dockerfile-anomalizer"),
                         image_name="anomalizer-service",
                         skip_push=True
                         )

app_port = 8056
prometheus_host = 'host.docker.internal:9090'
#prometheus_host = 'mini-prom-0:9090'
loki_url = 'http://host.docker.internal:3100'

I_SHARDS = 3
C_SHARDS = 1

containers = ['mini-prom', 'anomalizer-engine', 'anomalizer-images', 'anomalizer-correlator', 'anomalizer-api', 'load-test']
meta = {
    'instances': {'anomalizer-images': I_SHARDS, 'anomalizer-correlator': C_SHARDS},
    'ports':  {'mini-prom':9090, 'anomalizer-api': 8056, 'load-test': 7070},
    'endpoints': {'anomalizer-engine': 'http://anomalizer-engine:8060', 'anomalizer-images': 'http://anomalizer-images-{SHARD}:8061',
            'anomalizer-correlator': 'http://anomalizer-correlator-{SHARD}:8062'}
}

endpoints = [k.upper().replace('-', '_') + '=' + v for k,v in meta['endpoints'].items()]
print('endpoints=' + str(endpoints))

network = docker.Network("network", name="anomalizer-network", driver='bridge')

for container in containers:
    app_port = meta['ports'].get(container)
    _ports = []
    if app_port:
        _ports = [docker.ContainerPortArgs(internal=app_port, external=app_port)]
    instances = meta['instances'].get(container, 1)
    for instance in range(instances):
        docker.Container(container+'-'+str(instance),
            image=app_image.base_image_name,
            name=container+'-'+str(instance),
            entrypoints=['python', container+'.py'],
            ports=_ports,
            hostname=container,
            networks_advanced=[docker.ContainerNetworksAdvancedArgs(
                name=network.name
            )],
            envs=[
                f'PROMETHEUS={prometheus_host}',
                f'LOKI={loki_url}',
                f'I_SHARD={instance}',
                f'I_SHARDS={I_SHARDS}',
                f'C_SHARD={instance}',
                f'C_SHARDS={C_SHARDS}',
                'ANOMALIZER_IMAGES_PORT=8061',
                'ANOMALIZER_CORRELATOR_PORT=8062',
                'RESTORE_STATE=true',
            ] + endpoints
        )

pulumi.export("url", f"http://localhost:{app_port}")