mkdir -p data/{local-prometheus-data,prom0,prom1,prom2}

cat > data/bucket.yml << EOF
type: S3
config:
  bucket: thanos
  endpoint: minio:9000
  insecure: true
  signature_version2: true
  access_key: EXAMPLEKEY
  secret_key: EXAMPLESECRET
EOF

cat > data/local-prometheus-data/prometheus.yml << EOF
# When the Thanos remote-write-receive component is started,
# this is an example configuration of a Prometheus server that
# would scrape a local node-exporter and replicate its data to
# the remote write endpoint.
scrape_configs:
  - job_name: node
    scrape_interval: 1s
    static_configs:
    - targets: ['node-exporter:9100']
remote_write:
- url: http://receive:10908/api/v1/receive
EOF

cat > data/prom0/prometheus.yml << EOF
global:
  external_labels:
    prometheus: prom-0
scrape_configs:
- job_name: prometheus
  scrape_interval: 5s
  static_configs:
  - targets:
    - "localhost:9090"
- job_name: thanos-sidecar
  scrape_interval: 5s
  static_configs:
  - targets:
    - "sidecar0:10902"
- job_name: thanos-store
  scrape_interval: 5s
  static_configs:
  - targets:
    - "store:10906"
- job_name: thanos-receive
  scrape_interval: 5s
  static_configs:
  - targets:
    - "receive:10909"
- job_name: thanos-query
  scrape_interval: 5s
  static_configs:
  - targets:
    - "query0:10904"
    - "query1:10914"
EOF

cat > data/prom1/prometheus.yml << EOF
global:
  external_labels:
    prometheus: prom-1
scrape_configs:
- job_name: prometheus
  scrape_interval: 5s
  static_configs:
  - targets:
    - "localhost:9091"
- job_name: thanos-sidecar
  scrape_interval: 5s
  static_configs:
  - targets:
    - "sidecar1:10912"
- job_name: thanos-store
  scrape_interval: 5s
  static_configs:
  - targets:
    - "store:10906"
- job_name: thanos-receive
  scrape_interval: 5s
  static_configs:
  - targets:
    - "receive:10909"
- job_name: thanos-query
  scrape_interval: 5s
  static_configs:
  - targets:
    - "query0:10904"
    - "query1:10914"
EOF

cat > data/prom2/prometheus.yml << EOF
global:
  external_labels:
    prometheus: prom-2
scrape_configs:
- job_name: prometheus
  scrape_interval: 5s
  static_configs:
  - targets:
    - "localhost:9092"
- job_name: thanos-sidecar
  scrape_interval: 5s
  static_configs:
  - targets:
    - "sidecar2:10922"
- job_name: thanos-store
  scrape_interval: 5s
  static_configs:
  - targets:
    - "store:10906"
- job_name: thanos-receive
  scrape_interval: 5s
  static_configs:
  - targets:
    - "receive:10909"
- job_name: thanos-query
  scrape_interval: 5s
  static_configs:
  - targets:
    - "query0:10904"
    - "query1:10914"
EOF
