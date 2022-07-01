# GCP deployment

The Anomalizer deploys to a GCP AppEngine environment which auto-scales down to
zero when not in use. It deploys with an active load-test, and a mini-prom
engine to scrape the metrics from the services.  It uses the anomalizer-service.py
entry point to stand up the processes.