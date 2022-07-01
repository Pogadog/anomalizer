#!/bin/bash -x

# force a commit of the active files to get a traceable githash.

gcloud app deploy app.yaml  <<.
y
.

# update dispatch rules.
#./dispatch.sh
