"""A Python Pulumi program"""

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
loki_url = 'http://host.docker.internal:3100'

app_container = docker.Container("anomalizer-service",
                                 image=app_image.base_image_name,
                                 ports=[
                                     docker.ContainerPortArgs(internal=app_port, external=app_port)
                                 ],
                                 envs=[
                                     f'PROMETHEUS={prometheus_host}',
                                     f'LOKI={loki_url}'
                                 ]
                                 )

pulumi.export("url", f"http://localhost:{app_port}")