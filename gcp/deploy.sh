#!/bin/bash -x

# force a commit of the active files to get a traceable githash. Don't promote automatically to avoid
# broken deployments.

gcloud app deploy --no-promote app.yaml  <<.
y
.

# update dispatch rules.
#./dispatch.sh
